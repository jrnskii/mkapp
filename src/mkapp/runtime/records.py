"""Optional single-writer state and disposable cache, reached through bus actions."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mkapp.sdk import (
    ActionContext,
    AppError,
    NamespaceSpec,
    Registration,
    ResourceScope,
    StateSpec,
)

from .bus import ActionService
from .codec import Codec
from .config import Limits, check_id
from .persistence import StatePersistence


def request(
    value: Any, required: set[str], optional: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or value.keys() - required - optional
    ):
        raise AppError("invalid_payload")
    return value


class Namespaces:
    def __init__(self, limits: Limits) -> None:
        self.codec = Codec(limits)
        self.specs: dict[str, tuple[str, NamespaceSpec]] = {}

    def register(
        self, owner: str, scope: ResourceScope, spec: NamespaceSpec
    ) -> Registration:
        check_id(spec.id)
        if (
            len(self.specs) >= 256
            or not spec.id.startswith(owner + ".")
            or spec.id in self.specs
            or not callable(spec.validator)
            or type(spec.schema_version) is not int
            or not 1 <= spec.schema_version <= 1000
            or type(spec.max_entries) is not int
            or not 1 <= spec.max_entries <= 1024
            or type(spec.max_bytes) is not int
            or not 1 <= spec.max_bytes <= 8_388_608
            or not 0 < spec.ttl <= 86400
        ):
            raise AppError("invalid_registration")
        self.specs[spec.id] = owner, spec

        def dispose() -> None:
            self.specs.pop(spec.id, None)
            self.clear(spec.id)

        return scope.own(Registration(dispose))

    def authorize(self, owner: str, namespace: Any, key: Any) -> NamespaceSpec:
        if (
            type(namespace) is not str
            or type(key) is not str
            or not 1 <= len(key) <= 256
        ):
            raise AppError("invalid_payload")
        registered = self.specs.get(namespace)
        if registered is None or registered[0] != owner:
            raise AppError("permission_denied")
        return registered[1]

    def validate(self, spec: NamespaceSpec, value: Any) -> bytes:
        try:
            self.codec.encode(value)
            frame = self.codec.encode(spec.validator(value))
            if len(frame) > spec.max_bytes:
                raise ValueError
            return frame
        except Exception:
            raise AppError("invalid_payload") from None

    def clear(self, namespace: str) -> None:
        pass


@dataclass
class CacheEntry:
    frame: bytes
    expires: float


class Cache(Namespaces):
    def __init__(
        self, limits: Limits, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        super().__init__(limits)
        self.clock = clock
        self.entries: OrderedDict[tuple[str, str], CacheEntry] = OrderedDict()
        self.max_entries, self.max_bytes = 4096, 8_388_608

    def clear(self, namespace: str) -> None:
        for key in list(self.entries):
            if key[0] == namespace:
                del self.entries[key]

    def expire(self) -> None:
        for key, entry in list(self.entries.items()):
            if entry.expires <= self.clock():
                del self.entries[key]

    async def operate(self, operation: str, payload: Any, ctx: ActionContext) -> Any:
        fields = {"namespace", "key"} | ({"value"} if operation == "put" else set())
        payload = request(payload, fields)
        namespace, key = payload["namespace"], payload["key"]
        spec = self.authorize(ctx.source, namespace, key)
        self.expire()
        item_key = namespace, key
        if operation == "get":
            entry = self.entries.get(item_key)
            if entry is None:
                return {"hit": False, "value": None}
            self.entries.move_to_end(item_key)
            return {"hit": True, "value": self.codec.decode(entry.frame)}
        if operation == "delete":
            self.entries.pop(item_key, None)
            return {}
        frame = self.validate(spec, payload["value"])
        self.entries.pop(item_key, None)
        while True:
            local = [k for k in self.entries if k[0] == namespace]
            size = sum(len(self.entries[k].frame) for k in local)
            total = sum(len(e.frame) for e in self.entries.values())
            local_full = (
                len(local) >= spec.max_entries or size + len(frame) > spec.max_bytes
            )
            global_full = (
                len(self.entries) >= self.max_entries
                or total + len(frame) > self.max_bytes
            )
            if not local_full and not global_full:
                break
            victim = local[0] if local_full else next(iter(self.entries))
            del self.entries[victim]
        self.entries[item_key] = CacheEntry(frame, self.clock() + spec.ttl)
        return {}


class State:
    def __init__(self, limits: Limits) -> None:
        self.codec = Codec(limits)
        self.specs: dict[str, tuple[str, StateSpec]] = {}
        self.values: dict[str, bytes] = {}
        self.revisions: dict[str, int] = {}
        self.persistence: StatePersistence | None = None
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.notify: Any = None

    def register(
        self, owner: str, scope: ResourceScope, spec: StateSpec
    ) -> Registration:
        check_id(spec.key)
        if (
            len(self.specs) >= 1024
            or (spec.key not in self.revisions and len(self.revisions) >= 4096)
            or not spec.key.startswith(owner + ".")
            or spec.key in self.specs
            or not callable(spec.validator)
            or (spec.persist and self.persistence is None)
        ):
            raise AppError("invalid_registration")
        self.specs[spec.key] = owner, spec
        if spec.persist and self.persistence and spec.key in self.persistence.records:
            try:
                stored = self.persistence.records[spec.key]
                if (
                    type(stored) is not dict
                    or set(stored) != {"found", "value", "revision", "schema_version"}
                    or stored["schema_version"] != spec.schema_version
                    or type(stored["revision"]) is not int
                    or not 0 <= stored["revision"] < 2**63
                    or type(stored["found"]) is not bool
                ):
                    raise ValueError
                if stored["found"]:
                    self.values[spec.key] = self.codec.encode(
                        spec.validator(stored["value"])
                    )
                self.revisions[spec.key] = stored["revision"]
            except Exception:
                self.specs.pop(spec.key, None)
                raise AppError("invalid_registration") from None

        def dispose() -> None:
            self.specs.pop(spec.key, None)
            self.values.pop(spec.key, None)

        return scope.own(Registration(dispose))

    async def operate(self, operation: str, payload: Any, ctx: ActionContext) -> Any:
        if len(self.tasks) >= self.codec.limits.pending:
            raise AppError("busy")

        async def serialized() -> Any:
            async with self.lock:
                return await self.mutate(operation, payload, ctx)

        task = asyncio.create_task(serialized())
        self.tasks.add(task)

        def finished(t: asyncio.Task[Any]) -> None:
            self.tasks.discard(t)
            if not t.cancelled():
                t.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def mutate(self, operation: str, payload: Any, ctx: ActionContext) -> Any:
        if operation == "snapshot":
            request(payload, set())
            return {
                k: self.snapshot(k)
                for k, (owner, spec) in self.specs.items()
                if ctx.source == owner or ctx.source in spec.readers
            }
        fields = {"key"} | ({"value"} if operation == "set" else set())
        payload = request(
            payload, fields, {"expected_revision"} if operation != "get" else set()
        )
        key = payload["key"]
        if type(key) is not str or key not in self.specs:
            raise AppError("permission_denied")
        owner, spec = self.specs[key]
        grants = spec.readers if operation == "get" else spec.writers
        if ctx.source != owner and ctx.source not in grants:
            raise AppError("permission_denied")
        if operation == "get":
            return self.snapshot(key)
        expected = payload.get("expected_revision")
        if expected is not None and (
            type(expected) is not int or expected != self.revisions.get(key, 0)
        ):
            raise AppError("conflict")
        previous = self.values.get(key)
        revision = self.revisions.get(key, 0)
        if operation == "set":
            try:
                self.codec.encode(payload["value"])
                value = self.codec.encode(spec.validator(payload["value"]))
            except Exception:
                raise AppError("invalid_payload") from None
            if len(self.values) >= 1024 and key not in self.values:
                raise AppError("busy")
            if (
                sum(len(v) for k, v in self.values.items() if k != key) + len(value)
                > 8_388_608
            ):
                raise AppError("busy")
            self.values[key] = value
        else:
            self.values.pop(key, None)
        self.revisions[key] = self.revisions.get(key, 0) + 1
        if spec.persist and self.persistence is not None:
            try:
                await self.persistence.save(
                    key, {**self.snapshot(key), "schema_version": spec.schema_version}
                )
            except Exception:
                if previous is None:
                    self.values.pop(key, None)
                else:
                    self.values[key] = previous
                self.revisions[key] = revision
                raise AppError("internal_error") from None
        if self.notify is not None:
            await self.notify({"key": key, "revision": self.revisions[key]})
        return self.snapshot(key)

    async def close(self) -> None:
        if self.tasks:
            _, pending = await asyncio.wait(
                self.tasks, timeout=self.codec.limits.lifecycle_seconds
            )
            if pending:
                raise AppError("timeout")
        if self.persistence:
            self.persistence.close()

    def snapshot(self, key: str) -> dict[str, Any]:
        return {
            "found": key in self.values,
            "value": self.codec.decode(self.values[key])
            if key in self.values
            else None,
            "revision": self.revisions.get(key, 0),
        }


class RecordService:
    def __init__(
        self,
        actions: ActionService,
        scope: ResourceScope,
        feature: str,
        provider: Any = None,
    ) -> None:
        self.actions, self.scope, self.feature, self.provider = (
            actions,
            scope,
            feature,
            provider,
        )

    def register(self, spec: Any) -> Registration:
        self.actions.bus.assert_thread()
        if self.provider is None:
            raise AppError("feature_unavailable")
        return self.provider.register(self.actions.owner, self.scope, spec)

    async def call(self, operation: str, payload: Any) -> Any:
        if self.provider is None:
            raise AppError("feature_unavailable")
        target = f"mkapp.{self.feature}.{operation}.v1"
        return await self.actions.call(target, payload)

    async def get(self, namespace: str, key: str = "") -> Any:
        return await self.call(
            "get",
            {"key": namespace}
            if self.feature == "state"
            else {"namespace": namespace, "key": key},
        )

    async def put(self, namespace: str, key: str, value: Any, **options: Any) -> Any:
        if self.feature == "state":
            raise AppError("invalid_payload")
        return await self.call(
            "set" if self.feature == "state" else "put",
            {"namespace": namespace, "key": key, "value": value, **options},
        )

    async def set(
        self, key: str, value: Any, *, expected_revision: int | None = None
    ) -> Any:
        if self.feature != "state":
            raise AppError("feature_unavailable")
        return await self.call(
            "set", {"key": key, "value": value, "expected_revision": expected_revision}
        )

    async def delete(self, namespace: str, key: str = "", **options: Any) -> Any:
        return await self.call(
            "delete",
            {"key": namespace, **options}
            if self.feature == "state"
            else {"namespace": namespace, "key": key, **options},
        )
