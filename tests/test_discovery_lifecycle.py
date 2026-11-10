from __future__ import annotations

import sys

import pytest

from mkapp import AppConfig, Limits
from mkapp.runtime.discovery import discover, resolve
from mkapp.runtime.host import Host
from mkapp.sdk import AppError
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


def make_host(config):
    return Host(config, WindowHost(Bridge(config.limits)))


def test_selection_metadata_only_and_dependencies(tmp_path, plugin_tree, monkeypatch):
    plugin_tree("test.provider", "raise RuntimeError('must not import')")
    plugin_tree("test.consumer", requires={"test.provider": ">=0.1"})
    plugin_tree("test.unrelated")
    config = AppConfig("test.app", app_root=tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    items = discover(config)
    assert len(items) == 3
    assert not any(d.package in sys.modules for d in items.values())
    assert resolve(items, config).order == (
        "test.provider",
        "test.consumer",
        "test.unrelated",
    )
    assert (
        resolve(
            items, AppConfig("test.app", app_root=tmp_path, enabled_plugins=())
        ).order
        == ()
    )
    selected = resolve(
        items,
        AppConfig("test.app", app_root=tmp_path, enabled_plugins=("test.consumer",)),
    )
    assert selected.blocked == {"test.consumer": "missing_dependency"}
    assert (
        resolve(items, AppConfig("test.app", app_root=tmp_path, safe_mode=True)).order
        == ()
    )
    selected = resolve(
        items,
        AppConfig("test.app", app_root=tmp_path, toggles={"test.unrelated": False}),
    )
    assert set(selected.order) == {"test.provider", "test.consumer"}


def test_cycles_versions_and_symlinks(tmp_path, plugin_tree):
    plugin_tree("test.a", requires={"test.b": ">=0.1"})
    plugin_tree("test.b", requires={"test.a": ">=0.1"})
    config = AppConfig("test.app", app_root=tmp_path)
    with pytest.raises(AppError, match="dependency_cycle"):
        resolve(discover(config), config)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "plugins" / "escape").symlink_to(outside, target_is_directory=True)
    (outside / "mkapp-plugin.toml").write_text("", encoding="utf-8")
    with pytest.raises(AppError, match="unapproved_source"):
        discover(config)


async def test_noop_lifecycle_and_disabled_import(tmp_path, plugin_tree):
    plugin_tree("test.good")
    plugin_tree("test.disabled", "raise RuntimeError('disabled import')")
    config = AppConfig("test.app", app_root=tmp_path, enabled_plugins=("test.good",))
    host = make_host(config)
    try:
        await host.start()
        assert host.inventory["test.good"].status == "ready"
        assert host.inventory["test.disabled"].status == "disabled"
        assert host.inventory["test.disabled"].descriptor.package not in sys.modules
    finally:
        await host.stop()
    assert host.inventory["test.good"].status == "stopped"
    assert host.bus.context.closed
    assert not host.bus.running


async def test_partial_failure_cleanup_and_transitive_blocking(tmp_path, plugin_tree):
    plugin_tree(
        "test.bad",
        """
from mkapp.sdk import Plugin, Action
class Echo(Action):
    id = "test.bad.echo.v1"
    input = output = staticmethod(lambda v: v)
    async def handle(self, payload, ctx): return payload
class Bad(Plugin):
    def prepare(self, ctx): ctx.actions.register(Echo)
    async def start(self, ctx): raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")
    async def stop(self, ctx): raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")
def create_plugin(): return Bad()
""",
    )
    plugin_tree("test.consumer", requires={"test.bad": ">=0.1"})
    plugin_tree("test.independent")
    host = make_host(AppConfig("test.app", app_root=tmp_path))
    try:
        await host.start()
        assert host.inventory["test.bad"].status == "failed"
        assert host.inventory["test.bad"].error == "internal_error"
        assert not host.inventory["test.bad"].scope.active
        assert "test.bad.echo.v1" not in host.bus.bindings
        assert host.inventory["test.consumer"].status == "blocked"
        assert host.inventory["test.independent"].status == "ready"
    finally:
        await host.stop()


async def test_invalid_factory_and_stop_deadline(tmp_path, plugin_tree):
    plugin_tree("test.invalid", "def create_plugin(): return object()")
    plugin_tree(
        "test.slow",
        """
import asyncio
from mkapp.sdk import Plugin
class Slow(Plugin):
    async def stop(self, ctx): await asyncio.sleep(10)
def create_plugin(): return Slow()
""",
    )
    host = make_host(
        AppConfig("test.app", app_root=tmp_path, limits=Limits(lifecycle_seconds=0.02))
    )
    await host.start()
    assert host.inventory["test.invalid"].status == "failed"
    await host.stop()
    assert host.forced
    assert host.inventory["test.slow"].error == "timeout"
    await host.stop()
