"""Optional record store. Connection creation/use/closure stay on one thread."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from pathlib import Path
from typing import Any

from mkapp.sdk import ActionContext, AppError

from .config import Limits
from .records import Namespaces, request


class Storage(Namespaces):
    def __init__(self, limits: Limits, path: Path) -> None:
        super().__init__(limits)
        self.limits, self.path = limits, path
        self.queue: queue.Queue[Any] = queue.Queue(limits.pending)
        self.thread = threading.Thread(
            target=self.run, name="mkapp-storage", daemon=True
        )
        self.stopped: concurrent.futures.Future[None] = concurrent.futures.Future()
        self.closed = False
        self.owner_thread: int | None = None
        self.connection: Any = None
        self.thread.start()

    def initialize(self) -> None:
        import duckdb

        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.path.parent.chmod(0o700)
        existed = self.path.exists()
        self.connection = duckdb.connect(
            str(self.path),
            config={
                "enable_external_access": "false",
                "autoinstall_known_extensions": "false",
                "autoload_known_extensions": "false",
                "threads": "1",
                "memory_limit": "64MB",
                "max_temp_directory_size": "16MB",
                "temp_directory": str(self.path.parent / "tmp"),
            },
        )
        c = self.connection
        settings = c.execute(
            "SELECT current_setting('enable_external_access'), current_setting('autoinstall_known_extensions'), current_setting('autoload_known_extensions')"
        ).fetchone()
        if settings != (False, False, False):
            raise ValueError
        if not existed:
            c.begin()
            try:
                c.execute("CREATE TABLE mkapp_format (version INTEGER NOT NULL)")
                c.execute("INSERT INTO mkapp_format VALUES (1)")
                c.execute(
                    "CREATE TABLE records (namespace VARCHAR, key VARCHAR, schema_version INTEGER NOT NULL, value BLOB NOT NULL, PRIMARY KEY(namespace, key))"
                )
                c.commit()
            except Exception:
                c.rollback()
                raise
        if c.execute("SELECT version FROM mkapp_format").fetchall() != [(1,)]:
            raise ValueError
        self.path.chmod(0o600)

    def run(self) -> None:
        self.owner_thread = threading.get_ident()
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                operation, frame, future = item
                if future.cancelled():
                    continue
                try:
                    if operation == "init":
                        self.initialize()
                        result = {}
                    else:
                        result = self.execute(operation, self.codec.decode(frame))
                    future.set_result(self.codec.encode(result))
                except Exception:
                    if not future.done():
                        future.set_exception(AppError("feature_unavailable"))
        finally:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            self.stopped.set_result(None)

    def execute(self, operation: str, p: dict[str, Any]) -> Any:
        c = self.connection
        if c is None:
            raise ValueError
        namespace, key = p["namespace"], p["key"]
        if operation == "get":
            row = c.execute(
                "SELECT schema_version, value FROM records WHERE namespace=? AND key=?",
                [namespace, key],
            ).fetchone()
            if row is None:
                return {"found": False, "value": None}
            if row[0] != p["schema_version"]:
                raise ValueError
            return {"found": True, "value": self.codec.decode(row[1])}
        c.begin()
        try:
            if operation == "put":
                frame = p["frame"]
                count, size = c.execute(
                    "SELECT count(*), coalesce(sum(octet_length(value)), 0) FROM records WHERE namespace=? AND key<>?",
                    [namespace, key],
                ).fetchone()
                total_count, total_size = c.execute(
                    "SELECT count(*), coalesce(sum(octet_length(value)), 0) FROM records WHERE NOT (namespace=? AND key=?)",
                    [namespace, key],
                ).fetchone()
                if (
                    count >= p["max_entries"]
                    or size + len(frame) > p["max_bytes"]
                    or total_count >= 4096
                    or total_size + len(frame) > 8_388_608
                ):
                    raise ValueError
                c.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(namespace, key) DO UPDATE SET schema_version=excluded.schema_version, value=excluded.value",
                    [namespace, key, p["schema_version"], frame],
                )
            else:
                c.execute(
                    "DELETE FROM records WHERE namespace=? AND key=?", [namespace, key]
                )
            c.commit()
        except Exception:
            c.rollback()
            raise
        for child in self.path.parent.iterdir():
            if child.is_file():
                child.chmod(0o600)
        return {}

    async def submit(self, operation: str, payload: Any) -> Any:
        if self.closed:
            raise AppError("feature_unavailable")
        frame = self.codec.encode(payload)
        future: concurrent.futures.Future[bytes] = concurrent.futures.Future()
        try:
            self.queue.put_nowait((operation, frame, future))
        except queue.Full:
            raise AppError("busy") from None
        try:
            async with asyncio.timeout(self.limits.timeout_ms / 1000):
                return self.codec.decode(
                    await asyncio.shield(asyncio.wrap_future(future))
                )
        except TimeoutError:
            raise AppError("timeout") from None

    async def operate(self, operation: str, payload: Any, ctx: ActionContext) -> Any:
        p = request(
            payload, {"namespace", "key"} | ({"value"} if operation == "put" else set())
        )
        spec = self.authorize(ctx.source, p["namespace"], p["key"])
        frame = self.validate(spec, p["value"]) if operation == "put" else b""
        result = await self.submit(
            operation,
            {
                "namespace": p["namespace"],
                "key": p["key"],
                "schema_version": spec.schema_version,
                "frame": frame,
                "max_entries": spec.max_entries,
                "max_bytes": spec.max_bytes,
            },
        )
        if operation == "get" and result["found"]:
            self.validate(spec, result["value"])
        return result

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item and not item[2].done():
                item[2].set_exception(AppError("owner_failed"))
        self.queue.put_nowait(None)
        try:
            async with asyncio.timeout(self.limits.lifecycle_seconds):
                await asyncio.shield(asyncio.wrap_future(self.stopped))
        except TimeoutError:
            raise AppError("timeout") from None
