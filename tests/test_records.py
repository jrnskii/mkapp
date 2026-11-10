from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from mkapp import AppConfig, Limits
from mkapp.runtime.host import Host
from mkapp.runtime.records import Cache
from mkapp.runtime.storage import Storage
from mkapp.sdk import AppError, NamespaceSpec, ResourceScope, SecretValue
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


async def test_cache_expiry_lru_byte_pressure_null_and_owner_cleanup():
    now = [0.0]
    cache = Cache(Limits(), clock=lambda: now[0])
    scope = ResourceScope()
    cache.register(
        "test.owner",
        scope,
        NamespaceSpec(
            "test.owner.cache", lambda v: v, max_entries=2, max_bytes=16, ttl=5
        ),
    )
    ctx = SimpleNamespace(source="test.owner")

    async def call(op, key, **kw):
        return await cache.operate(
            op, {"namespace": "test.owner.cache", "key": key, **kw}, ctx
        )

    await call("put", "a", value=None)
    assert await call("get", "a") == {"hit": True, "value": None}
    await call("put", "b", value="123456")
    await call("get", "a")
    await call("put", "c", value="123456")
    assert not (await call("get", "b"))["hit"]
    await call("put", "d", value="12345678901234")
    assert len(cache.entries) == 1
    now[0] = 6
    assert not (await call("get", "d"))["hit"]
    with pytest.raises(AppError):
        await cache.operate(
            "get",
            {"namespace": "test.owner.cache", "key": "a"},
            SimpleNamespace(source="test.other"),
        )
    with pytest.raises(AppError):
        await call("put", "private", value=SecretValue("SYNTHETIC"))
    scope.close()
    assert not cache.entries and not cache.specs


async def test_state_and_cache_real_bus_independent_extensions(tmp_path, plugin_tree):
    plugin_tree(
        "test.records",
        """
from mkapp.sdk import Plugin, StateSpec, NamespaceSpec
class Records(Plugin):
    def prepare(self, ctx):
        ctx.state.register(StateSpec("test.records.value", lambda v: v))
        ctx.cache.register(NamespaceSpec("test.records.cache", lambda v: v))
def create_plugin(): return Records()
""",
    )
    config = AppConfig("test.app", app_root=tmp_path, extensions=("state", "cache"))
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        entry = host.inventory["test.records"]
        assert entry.status == "ready"
        actions = entry.context.actions
        result = await actions.call(
            "mkapp.state.set.v1",
            {"key": "test.records.value", "value": None, "expected_revision": 0},
        )
        assert result == {"found": True, "value": None, "revision": 1}
        with pytest.raises(AppError, match="conflict"):
            await actions.call(
                "mkapp.state.set.v1",
                {"key": "test.records.value", "value": 2, "expected_revision": 0},
            )
        await actions.call(
            "mkapp.state.delete.v1",
            {"key": "test.records.value", "expected_revision": 1},
        )
        result = await actions.call(
            "mkapp.state.set.v1",
            {"key": "test.records.value", "value": 3, "expected_revision": 2},
        )
        assert result["revision"] == 3
        await entry.context.cache.put("test.records.cache", "a", None)
        assert await entry.context.cache.get("test.records.cache", "a") == {
            "hit": True,
            "value": None,
        }
        assert not entry.context.features.has("local_storage")
        with pytest.raises(AppError, match="feature_unavailable"):
            await entry.context.local_storage.get("test.records.store", "a")
    finally:
        await host.stop()


async def test_duckdb_reopen_quota_rollback_and_owner_thread(tmp_path):
    pytest.importorskip("duckdb")
    path = tmp_path / "storage/records.duckdb"

    async def open_store():
        storage = Storage(Limits(), path)
        await storage.submit("init", {})
        storage.register(
            "test.owner",
            ResourceScope(),
            NamespaceSpec("test.owner.store", lambda v: v, max_entries=1),
        )
        return storage

    storage = await open_store()
    ctx = SimpleNamespace(source="test.owner")
    payload = {"namespace": "test.owner.store", "key": "a"}
    try:
        assert storage.owner_thread != threading.get_ident()
        await storage.operate("put", {**payload, "value": None}, ctx)
        with pytest.raises(AppError):
            await storage.operate("put", {**payload, "key": "b", "value": 42}, ctx)
        with pytest.raises(AppError):
            await storage.operate(
                "put", {**payload, "value": SecretValue("SYNTHETIC")}, ctx
            )
        assert await storage.operate("get", payload, ctx) == {
            "found": True,
            "value": None,
        }
    finally:
        await storage.close()
    assert not storage.thread.is_alive()
    storage = await open_store()
    try:
        assert await storage.operate("get", payload, ctx) == {
            "found": True,
            "value": None,
        }
        await storage.operate("delete", payload, ctx)
        assert not (await storage.operate("get", payload, ctx))["found"]
    finally:
        await storage.close()
