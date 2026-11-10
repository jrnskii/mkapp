"""Explicit bounded async/I/O-thread/spawned-process jobs; no secret objects."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import multiprocessing
import threading
from dataclasses import dataclass
from typing import Any

from mkapp.sdk import AppError, Registration, ResourceScope

from .codec import Codec
from .config import Limits, check_id
from .discovery import ENTRY


def execute_job(entry: str, frame: bytes, limits: Limits) -> bytes:
    """Spawn-safe adapter. Only trusted entry strings and MessagePack bytes transfer."""
    codec = Codec(limits)
    module, name = entry.split(":")
    try:
        result = getattr(importlib.import_module(module), name)(codec.decode(frame))
        return codec.encode(result)
    except Exception:
        raise AppError("internal_error") from None


@dataclass
class Job:
    owner: str
    entry: str
    kind: str
    concurrency: int
    active: int = 0


class WorkerPool:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.codec = Codec(limits)
        self.thread = threading.get_ident()
        self.jobs: dict[str, Job] = {}
        self.tasks: dict[asyncio.Task[Any], str] = {}
        self.thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, limits.concurrency), thread_name_prefix="mkapp-io"
        )
        self.process_pool: concurrent.futures.ProcessPoolExecutor | None = None
        self.futures: set[concurrent.futures.Future[Any]] = set()
        self.closed = False
        self.shutdown_timeout = False

    def assert_thread(self) -> None:
        if threading.get_ident() != self.thread:
            raise AppError("permission_denied")

    def register(
        self,
        owner: str,
        scope: ResourceScope,
        name: str,
        entry: str,
        kind: str,
        concurrency: int,
    ) -> Registration:
        self.assert_thread()
        check_id(name)
        if (
            not name.startswith(owner + ".")
            or name in self.jobs
            or kind not in ("async", "thread", "process")
            or not ENTRY.fullmatch(entry)
            or type(concurrency) is not int
            or not 1 <= concurrency <= min(8, self.limits.concurrency)
        ):
            raise AppError("invalid_registration")
        self.jobs[name] = Job(owner, entry, kind, concurrency)

        def dispose() -> None:
            self.jobs.pop(name, None)

        return scope.own(Registration(dispose))

    async def submit(self, owner: str, name: str, payload: Any, timeout_ms: int) -> Any:
        self.assert_thread()
        job = self.jobs.get(name)
        if self.closed or job is None or job.owner != owner:
            raise AppError("permission_denied")
        if (
            job.active >= job.concurrency
            or len(self.futures) >= self.limits.concurrency
            or sum(j.active for j in self.jobs.values() if j.owner == owner)
            >= self.limits.concurrency
        ):
            raise AppError("busy")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise AppError("invalid_payload")
        frame = self.codec.encode(payload)
        job.active += 1
        task = asyncio.current_task()
        assert task is not None
        self.tasks[task] = owner
        deferred_release = False
        try:
            async with asyncio.timeout(min(timeout_ms, self.limits.timeout_ms) / 1000):
                if job.kind == "async":
                    module, fn = job.entry.split(":")
                    callback = getattr(importlib.import_module(module), fn)
                    if not inspect.iscoroutinefunction(callback):
                        raise AppError("invalid_payload")
                    result = await callback(self.codec.decode(frame))
                    return self.codec.decode(self.codec.encode(result))
                pool: Any = self.thread_pool
                if job.kind == "process":
                    if self.process_pool is None:
                        self.process_pool = concurrent.futures.ProcessPoolExecutor(
                            max_workers=min(4, self.limits.concurrency),
                            mp_context=multiprocessing.get_context("spawn"),
                        )
                    pool = self.process_pool
                future = pool.submit(execute_job, job.entry, frame, self.limits)
                self.futures.add(future)
                deferred_release = True
                loop = asyncio.get_running_loop()

                def released() -> None:
                    self.futures.discard(future)
                    job.active -= 1

                future.add_done_callback(
                    lambda _: (
                        loop.call_soon_threadsafe(released)
                        if not loop.is_closed()
                        else None
                    )
                )
                # Keep admission until the actual work ends, even after caller cancellation.
                result = await asyncio.shield(asyncio.wrap_future(future))
                return self.codec.decode(result)
        except TimeoutError:
            raise AppError("timeout") from None
        except AppError:
            raise
        except Exception:
            raise AppError("internal_error") from None
        finally:
            if not deferred_release:
                job.active -= 1
            self.tasks.pop(task, None)

    async def remove_owner(self, owner: str) -> None:
        tasks = [
            t
            for t, o in self.tasks.items()
            if o == owner and t is not asyncio.current_task()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.limits.lifecycle_seconds)

    async def close(self) -> None:
        self.closed = True
        for owner in set(self.tasks.values()):
            await self.remove_owner(owner)
        self.thread_pool.shutdown(wait=False, cancel_futures=True)
        pending = [asyncio.wrap_future(f) for f in self.futures if not f.done()]
        if pending:
            _, unfinished = await asyncio.wait(
                pending, timeout=self.limits.lifecycle_seconds
            )
            self.shutdown_timeout = bool(unfinished)
        if self.process_pool:
            if self.shutdown_timeout:
                self.process_pool.terminate_workers()
            else:
                self.process_pool.shutdown(wait=False, cancel_futures=True)


class WorkerService:
    def __init__(
        self, pool: WorkerPool, owner: str, scope: ResourceScope | None
    ) -> None:
        self.pool, self.owner, self.scope = pool, owner, scope

    def register(
        self, name: str, entry: str, *, kind: str, max_concurrency: int = 1
    ) -> Registration:
        if self.scope is None:
            raise AppError("permission_denied")
        return self.pool.register(
            self.owner, self.scope, name, entry, kind, max_concurrency
        )

    async def submit(self, name: str, payload: Any, *, timeout_ms: int = 5000) -> Any:
        return await self.pool.submit(self.owner, name, payload, timeout_ms)
