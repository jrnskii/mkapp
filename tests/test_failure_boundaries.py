from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path
from types import SimpleNamespace

import pytest

from mkapp import AppConfig
from mkapp.runtime.bus import ActionService, Bus
from mkapp.runtime.discovery import discover, resolve
from mkapp.runtime.host import Host
from mkapp.sdk import Action, AppError, ResourceScope
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


def test_duplicate_package_id_versions_cycles_and_nested_scans(tmp_path, plugin_tree):
    plugin_tree("test.a")
    b = plugin_tree("test.b", requires={"test.a": ">=2"})
    config = AppConfig("test.app", app_root=tmp_path)
    result = resolve(discover(config), config)
    assert result.blocked["test.b"] == "incompatible_dependency"
    descriptor = b / "mkapp-plugin.toml"
    descriptor.write_text(
        descriptor.read_text().replace('id = "test.b"', 'id = "test.a"'),
        encoding="utf-8",
    )
    with pytest.raises(AppError, match="duplicate_plugin"):
        discover(config)
    descriptor.unlink()
    nested = tmp_path / "plugins/group/nested"
    nested.mkdir(parents=True)
    (nested / "mkapp-plugin.toml").write_text("invalid nested TOML", encoding="utf-8")
    assert set(discover(config)) == {"test.a"}
    assert not discover(AppConfig("test.app", app_root=tmp_path / "missing"))


def test_wheel_metadata_mismatch_never_loads_entrypoint(
    tmp_path, plugin_tree, monkeypatch
):
    root = plugin_tree("test.wheel", "raise RuntimeError('must not import')")
    from mkapp.runtime.discovery import descriptor

    item = descriptor(root / "mkapp-plugin.toml", root)
    package = root / item.package
    (package / "mkapp-plugin.toml").write_bytes(
        (root / "mkapp-plugin.toml").read_bytes()
    )
    entry = SimpleNamespace(group="mkapp.plugins", name="test.wheel", value=item.entry)
    fake = SimpleNamespace(
        entry_points=[entry],
        files=[
            Path(item.package) / "__init__.py",
            Path(item.package) / "mkapp-plugin.toml",
        ],
        locate_file=lambda p: root / p,
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: fake)
    config = AppConfig(
        "test.app",
        app_root=tmp_path / "empty",
        approved_distributions=("synthetic-wheel",),
    )
    assert set(discover(config)) == {"test.wheel"}
    entry.name = "test.mismatch"
    with pytest.raises(AppError, match="invalid_metadata"):
        discover(config)


async def test_response_validation_permission_and_direct_reentrancy():
    bus = Bus(AppConfig("test.app"))
    scope = ResourceScope()
    for owner in ("test.provider", "test.consumer"):
        bus.add_owner(owner)
        bus.ready.add(owner)
    service = ActionService(bus, "test.provider", scope)

    class BadOutput(Action):
        id = "test.provider.bad.v1"
        input = staticmethod(lambda v: v)
        output = staticmethod(lambda v: int(v))

        async def handle(self, payload, ctx):
            return "SYNTHETIC-PRIVATE-SENTINEL"

    class Recursive(Action):
        id = "test.provider.recursive.v1"
        input = output = staticmethod(lambda v: v)

        async def handle(self, payload, ctx):
            return await ctx.actions.call(Recursive, payload)

    try:
        service.register(BadOutput)
        service.register(Recursive)
        with pytest.raises(AppError, match="invalid_payload"):
            await service.call(BadOutput, {})
        with pytest.raises(AppError, match="invalid_payload"):
            await service.call(Recursive, {})
        with pytest.raises(AppError, match="permission_denied"):
            await ActionService(bus, "test.consumer", ResourceScope()).call(
                BadOutput, {}
            )
    finally:
        scope.close()
        await bus.close()


async def test_cancelled_startup_cleans_prepared_scopes(tmp_path, plugin_tree):
    plugin_tree(
        "test.slow",
        """
import asyncio
from mkapp.sdk import Plugin
class Slow(Plugin):
    async def start(self, ctx): await asyncio.sleep(30)
def create_plugin(): return Slow()
""",
    )
    config = AppConfig("test.app", app_root=tmp_path)
    host = Host(config, WindowHost(Bridge(config.limits)))
    startup = asyncio.create_task(host.start())
    while not host.inventory or not host.inventory["test.slow"].started:
        await asyncio.sleep(0)
    startup.cancel()
    await asyncio.gather(startup, return_exceptions=True)
    await host.stop()
    assert not host.inventory["test.slow"].scope.active
    assert host.closed and host.bus.context.closed


async def test_application_composition_provider_unchanged_when_viewer_disabled():
    root = Path(__file__).resolve().parents[1] / "examples/market"
    results = []
    for selection in (("example.prices",), None, ("example.prices",)):
        config = AppConfig("test.composition", app_root=root, enabled_plugins=selection)
        host = Host(config, WindowHost(Bridge(config.limits)))
        try:
            await host.start()
            assert host.inventory["example.prices"].status == "ready"
            results.append(
                await host.inventory["example.prices"].context.actions.call(
                    "example.prices.series.v1", {"count": 3}
                )
            )
            if selection is None:
                assert host.inventory["example.viewer"].status == "ready"
        finally:
            await host.stop()
    assert results[0] == results[1] == results[2]


async def test_runnable_workbench_uses_all_optional_record_services(tmp_path):
    pytest.importorskip("duckdb")
    root = Path(__file__).resolve().parents[1] / "examples/workbench"
    config = AppConfig(
        "test.workbench",
        app_root=root,
        data_root=tmp_path,
        extensions=(
            "state",
            "state_persistence",
            "cache",
            "local_storage",
            "palette",
            "autocomplete",
        ),
    )
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        entry = host.inventory["example.workbench"]
        assert entry.status == "ready", entry.error
        result = await entry.context.actions.call(
            "example.workbench.calculate.v1", {"count": 100}
        )
        assert result == {"total": sum(i * i for i in range(100))}
        assert (await entry.context.state.get("example.workbench.runs"))["value"] == 1
        assert (await entry.context.cache.get("example.workbench.cache", "nullable"))[
            "hit"
        ]
        assert (
            await entry.context.local_storage.get("example.workbench.presets", "demo")
        )["found"]
    finally:
        await host.stop()
    assert not host.shutdown_errors
    assert not host.private.handles
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"SYNTHETIC-PRIVATE-INPUT" not in path.read_bytes()
