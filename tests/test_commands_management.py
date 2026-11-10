from __future__ import annotations

from types import SimpleNamespace

import pytest

from mkapp import AppConfig
from mkapp.runtime.commands import Catalog, ParseError
from mkapp.runtime.config import preferences
from mkapp.runtime.host import Host
from mkapp.sdk import AppError, CommandField, CommandSpec, ResourceScope
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


def catalog():
    c = Catalog()
    c.register(
        "test.workspace",
        ResourceScope(),
        CommandSpec(
            "test.workspace.open.v1",
            ("workspace", "open"),
            "test.workspace.action.v1",
            {
                "name": CommandField("string"),
                "read_only": CommandField("boolean", required=False, default=True),
            },
        ),
    )
    return c


def test_command_grammar_unicode_escapes_defaults_and_spans():
    c = catalog()
    spec, values = c.parse('workspace open name="Tyrimas \\"λ\\"" read_only=false')
    assert spec.id == "test.workspace.open.v1"
    assert values == {"name": 'Tyrimas "λ"', "read_only": False}
    assert c.parse('workspace open name="Research"')[1]["read_only"] is True
    for text in (
        'workspace open name="a" name="b"',
        "workspace open bad=true",
        "workspace open read_only=1",
        'workspace open name="unterminated',
        'workspace open name=__import__("os")',
        'workspace open name="a"; x=1',
    ):
        with pytest.raises(ParseError) as error:
            c.parse(text)
        assert 0 <= error.value.start <= error.value.end <= len(text)


def test_completion_in_middle_uses_insertion_span_and_limits():
    c = catalog()
    text = 'workspace open read_only=f name="Later"'
    cursor = text.index("f") + 1
    matches = c.suggest(text, cursor)
    selected = next(m for m in matches if m["text"] == "false")
    assert (
        text[: selected["start"]] + selected["text"] + text[selected["end"] :]
        == 'workspace open read_only=false name="Later"'
    )
    assert c.suggest("workspace o", 11)[0]["text"] == "workspace open"
    assert len(c.suggest("", 0)) <= 20


async def test_manager_proposes_closure_and_commits_for_restart(tmp_path, plugin_tree):
    plugin_tree("test.provider", extra="announce = true\n")
    plugin_tree("test.consumer", requires={"test.provider": ">=0.1"})
    config = AppConfig(
        "test.app",
        app_root=tmp_path,
        data_root=tmp_path / "data",
        extensions=("plugin_manager", "registry_announcements"),
    )
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        manager = host.inventory["mkapp.manager"].context.actions
        proposal = await manager.call(
            "mkapp.manager.propose.v1", {"toggles": {"test.provider": False}}
        )
        assert proposal["toggles"] == {"test.consumer": False, "test.provider": False}
        await manager.call("mkapp.manager.commit.v1", {"toggles": proposal["toggles"]})
        assert host.inventory["test.consumer"].status == "ready"
        assert host.inventory["test.consumer"].desired is False
        loaded = preferences(config, config.data_dir / "preferences.json")
        assert not loaded.toggles["test.provider"]
        registry = await host.management.registry(
            {}, SimpleNamespace(source="mkapp.registry")
        )
        assert registry["revision"] >= 1
        assert [p["id"] for p in registry["plugins"]] == ["test.provider"]
        with pytest.raises(AppError, match="feature_unavailable"):
            await manager.call("mkapp.manager.restart.v1", {})
    finally:
        await host.stop()


async def test_manager_cannot_widen_allowlist(tmp_path, plugin_tree):
    plugin_tree("test.provider")
    plugin_tree("test.consumer", requires={"test.provider": ">=0.1"})
    config = AppConfig(
        "test.app",
        app_root=tmp_path,
        data_root=tmp_path / "data",
        enabled_plugins=("test.consumer",),
        extensions=("plugin_manager",),
    )
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        manager = host.inventory["mkapp.manager"].context.actions
        with pytest.raises(AppError, match="conflict"):
            await manager.call(
                "mkapp.manager.propose.v1", {"toggles": {"test.consumer": True}}
            )
        assert host.inventory["test.provider"].status == "disabled"
    finally:
        await host.stop()
