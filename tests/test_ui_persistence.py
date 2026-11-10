from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from mkapp import AppConfig, Limits
from mkapp.runtime.bus import ActionService, Bus
from mkapp.runtime.host import Host
from mkapp.runtime.persistence import StatePersistence
from mkapp.runtime.records import State
from mkapp.sdk import AppError, ResourceScope, StateSpec, Window
from mkapp.ui.bridge import Bridge
from mkapp.ui.components import ComponentHost
from mkapp.ui.windows import WindowHost, WindowService


async def test_durable_state_reopen_delete_recreate_and_failed_write(tmp_path):
    path = tmp_path / "state/state.msgpack"
    ctx = SimpleNamespace(source="test.owner")

    async def open_state():
        provider = State(Limits())
        provider.persistence = StatePersistence(path, Limits())
        await provider.persistence.load()
        provider.register(
            "test.owner",
            ResourceScope(),
            StateSpec("test.owner.setting", lambda v: v, persist=True),
        )
        return provider

    provider = await open_state()
    await provider.operate("set", {"key": "test.owner.setting", "value": None}, ctx)
    await provider.close()
    assert path.stat().st_mode & 0o777 == 0o600
    provider = await open_state()
    try:
        assert await provider.operate("get", {"key": "test.owner.setting"}, ctx) == {
            "found": True,
            "value": None,
            "revision": 1,
        }
        await provider.operate(
            "delete", {"key": "test.owner.setting", "expected_revision": 1}, ctx
        )
        await provider.operate(
            "set",
            {"key": "test.owner.setting", "value": 3, "expected_revision": 2},
            ctx,
        )
        with pytest.raises(AppError, match="conflict"):
            await provider.operate(
                "set",
                {"key": "test.owner.setting", "value": 4, "expected_revision": 1},
                ctx,
            )

        async def broken(*args):
            raise OSError("SYNTHETIC-PRIVATE-SENTINEL")

        provider.persistence.save = broken
        with pytest.raises(AppError, match="internal_error"):
            await provider.operate(
                "set", {"key": "test.owner.setting", "value": 9}, ctx
            )
        assert provider.snapshot("test.owner.setting")["value"] == 3
    finally:
        await provider.close()


async def test_corrupt_persistence_disables_service_before_dependent_import(
    tmp_path, plugin_tree
):
    plugin_tree(
        "test.required",
        "raise RuntimeError('must not import')",
        extra='required_features = ["state"]\n',
    )
    plugin_tree("test.independent")
    config = AppConfig(
        "test.app",
        app_root=tmp_path,
        data_root=tmp_path / "data",
        extensions=("state", "state_persistence"),
    )
    path = config.data_dir / "state/state.msgpack"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"corrupt-synthetic-format")
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        assert host.inventory["test.required"].status == "blocked"
        assert host.inventory["test.independent"].status == "ready"
        assert host.feature_errors["state"] == "feature_unavailable"
        assert path.read_bytes() == b"corrupt-synthetic-format"
    finally:
        await host.stop()


async def test_window_instances_hooks_scopes_and_failed_hook_cleanup():
    config = AppConfig("test.app")
    bridge = Bridge(config.limits)
    host = WindowHost(bridge)
    host.components = ComponentHost(bridge)
    bus = Bus(config)
    bus.add_owner("test.window")
    scope = ResourceScope()
    service = WindowService(
        host, "test.window", scope, ActionService(bus, "test.window", scope)
    )
    observed = []

    class Counter(Window):
        id, title = "test.window.counter.v1", "Synthetic"

        def __init__(self):
            self.count = 0
            observed.append(threading.get_ident())

        def render(self, ctx):
            self.count += 1

        def on_close(self, ctx):
            observed.append(threading.get_ident())
            raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")

    try:
        handle = service.register(Counter)
        record = host.registrations[Counter.id]
        first, second = host.open(record, {"values": [1]}), host.open(record, {})
        assert host.windows[first].window is not host.windows[second].window
        with pytest.raises(TypeError):
            host.windows[first].context.arguments["new"] = 2
        host.close(first)
        host.close(first)
        assert first not in host.windows and second in host.windows
        handle.close()
        assert not record.active
        host.close_all()
        assert all(t == threading.get_ident() for t in observed)

        class Failed(Counter):
            id = "test.window.failed.v1"

            def on_open(self, ctx):
                raise ValueError("synthetic")

        service.register(Failed)
        with pytest.raises(AppError, match="internal_error"):
            host.open(host.registrations[Failed.id], {})
        assert not host.windows
    finally:
        scope.close()
        await bus.close()


async def test_bridge_is_bounded_and_discards_closed_window_callbacks():
    bridge = Bridge(Limits(gui_queue=1))
    active, delivered = [True], []

    async def call(target, payload, **kwargs):
        return {"value": payload["value"]}

    bridge.submit(
        asyncio.get_running_loop(),
        call,
        lambda: active[0],
        "test.action.v1",
        {"value": 1},
        on_success=delivered.append,
        on_error=delivered.append,
    )
    with pytest.raises(AppError, match="busy"):
        bridge.submit(
            asyncio.get_running_loop(),
            call,
            lambda: True,
            "test.action.v1",
            {},
            on_success=delivered.append,
            on_error=delivered.append,
        )
    active[0] = False
    for _ in range(4):
        await asyncio.sleep(0)
    bridge.drain()
    assert not delivered
    assert bridge.slots.acquire(blocking=False)
