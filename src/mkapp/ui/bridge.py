"""Bounded cross-thread directives and completions, delivered at frame boundaries."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
import time
from typing import Any, Callable

from mkapp.runtime.bus import safe_code
from mkapp.runtime.codec import Codec
from mkapp.runtime.config import Limits
from mkapp.sdk import AppError


class GuiRequest:
    """GUI-owned request cancellation; cancellation is best effort."""

    def __init__(self, future: concurrent.futures.Future[Any]) -> None:
        self._future = future

    def cancel(self) -> None:
        self._future.cancel()


class Bridge:
    def __init__(self, limits: Limits) -> None:
        self.thread = threading.get_ident()
        self.directives: queue.Queue[Callable[[], None]] = queue.Queue(limits.gui_queue)
        self.completions: queue.Queue[tuple[Any, ...]] = queue.Queue(limits.gui_queue)
        self.slots = threading.BoundedSemaphore(limits.gui_queue)
        self.codec = Codec(limits)
        self.closed = False

    def assert_gui(self) -> None:
        if threading.get_ident() != self.thread:
            raise AppError("permission_denied")

    def post(self, fn: Callable[[], None]) -> None:
        try:
            if self.closed:
                raise AppError("owner_failed")
            self.directives.put_nowait(fn)
        except queue.Full:
            raise AppError("busy") from None

    def submit(
        self,
        loop: asyncio.AbstractEventLoop,
        call: Callable[..., Any],
        valid: Callable[[], bool],
        target: Any,
        payload: Any,
        *,
        on_success: Callable[[Any], None],
        on_error: Callable[[AppError], None],
        timeout_ms: int = 5000,
    ) -> GuiRequest:
        self.assert_gui()
        if self.closed or not valid():
            raise AppError("owner_failed")
        # Snapshot GUI payload before entering another execution domain.
        snapshot = self.codec.decode(self.codec.encode(payload))
        if not self.slots.acquire(blocking=False):
            raise AppError("busy")
        coroutine = call(target, snapshot, timeout_ms=timeout_ms)
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except Exception:
            coroutine.close()
            self.slots.release()
            raise AppError("owner_failed") from None

        def completed(result: concurrent.futures.Future[Any]) -> None:
            try:
                value, error = result.result(), None
            except BaseException as exc:
                value, error = (
                    None,
                    AppError("cancelled" if result.cancelled() else safe_code(exc)),
                )
            # The slot is held until GUI delivery; at most capacity entries exist.
            self.completions.put_nowait((valid, on_success, on_error, value, error))

        future.add_done_callback(completed)
        return GuiRequest(future)

    def drain(self) -> None:
        self.assert_gui()
        deadline = time.monotonic() + 0.002
        for _ in range(128):
            if time.monotonic() >= deadline:
                break
            try:
                valid, success, failure, value, error = self.completions.get_nowait()
            except queue.Empty:
                break
            try:
                if valid():
                    failure(error) if error else success(value)
            except Exception:
                pass
            finally:
                self.slots.release()
        for _ in range(128):
            if time.monotonic() >= deadline:
                break
            try:
                fn = self.directives.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                pass
