"""Small schema-versioned atomic state snapshots, with no plaintext private fallback."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import tempfile
from pathlib import Path
from typing import Any

from mkapp.sdk import AppError

from .codec import Codec
from .config import Limits


class StatePersistence:
    def __init__(self, path: Path, limits: Limits) -> None:
        self.path, self.codec = path, Codec(limits)
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mkapp-state"
        )
        self.records: dict[str, Any] = {}

    async def load(self) -> None:
        def read() -> bytes | None:
            if not self.path.exists():
                return None
            if self.path.stat().st_size > self.codec.limits.frame_bytes:
                raise ValueError
            return self.path.read_bytes()

        try:
            frame = await asyncio.wrap_future(self.pool.submit(read))
            if frame is None:
                return
            value = self.codec.decode(frame)
            if (
                type(value) is not dict
                or set(value) != {"schema_version", "keys"}
                or value["schema_version"] != 1
                or type(value["keys"]) is not dict
            ):
                raise ValueError
            self.records = value["keys"]
        except Exception:
            raise AppError("feature_unavailable") from None

    async def save(self, key: str, record: Any) -> None:
        snapshot = {**self.records, key: record}
        frame = self.codec.encode({"schema_version": 1, "keys": snapshot})

        def write() -> None:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".mkapp-", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(frame)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)

        try:
            await asyncio.wrap_future(self.pool.submit(write))
        except Exception:
            raise AppError("internal_error") from None
        self.records = snapshot

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
