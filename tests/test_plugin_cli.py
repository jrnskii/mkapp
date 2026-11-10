from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from mkapp import AppConfig
from mkapp.cli import main
from mkapp.runtime.discovery import discover, resolve
from mkapp.runtime.host import Host
from mkapp.scaffold import ScaffoldError, create_plugin
from mkapp.sdk import ActionRef, AppError
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


@pytest.fixture
def app(tmp_path):
    root = tmp_path / "myapp"
    root.mkdir()
    yield root
    for name in tuple(sys.modules):
        if name.startswith("myapp_"):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not p.startswith(str(root))]


@pytest.mark.parametrize("flag", ["-t", "--template"])
def test_cli_prefix_and_template_options(app, monkeypatch, capsys, flag):
    monkeypatch.chdir(app)
    monkeypatch.setattr(
        sys, "argv", ["mkapp", "plugin", "create", "example", flag, "data"]
    )
    main()
    item = discover(AppConfig("test.app", app_root=app))["myapp.example"]
    assert item.package == "myapp_example"
    assert "@action" in (item.root / item.package / "__init__.py").read_text()
    assert "Created myapp.example" in capsys.readouterr().out


def test_metadata_naming_overrides_and_dry_run(app):
    (app / "pyproject.toml").write_text('[project]\nname = "Research-App"\n')
    assert (
        create_plugin("example", app_root=app, dry_run=True).name
        == "research_app.example"
    )
    assert not (app / "plugins").exists()
    assert not list(app.glob(".mkapp-create-*"))
    assert (
        create_plugin("example", app_root=app, app_name="myapp").name == "myapp.example"
    )


@pytest.mark.parametrize("name", ["../bad", "Bad", "a-b", "a..b", "", "a" * 160])
def test_invalid_names_leave_no_files(app, name):
    with pytest.raises(ScaffoldError):
        create_plugin(name, app_root=app)
    assert not (app / "plugins").exists()


def test_no_overwrite_package_collision_or_escape(app, tmp_path):
    target = create_plugin("a_b", app_root=app)
    before = (target / "mkapp-plugin.toml").read_bytes()
    for name in ("a_b", "a.b"):
        with pytest.raises(ScaffoldError):
            create_plugin(name, app_root=app)
    assert (target / "mkapp-plugin.toml").read_bytes() == before
    empty = app / "plugins" / "myapp.empty"
    empty.mkdir()
    with pytest.raises(ScaffoldError):
        create_plugin("empty", app_root=app)
    assert empty.is_dir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (app / "plugins" / "myapp.link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ScaffoldError):
        create_plugin("link", app_root=app)
    other = tmp_path / "other"
    other.mkdir()
    (other / "plugins").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ScaffoldError):
        create_plugin("example", app_root=other, app_name="myapp")
    assert not list(outside.iterdir())


def test_publication_failure_cleans_up_only_owned_target(app, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic private error")

    monkeypatch.setattr(os, "link", fail)
    with pytest.raises(ScaffoldError, match="Could not create plugin"):
        create_plugin("broken", app_root=app)
    assert not list((app / "plugins").iterdir())
    assert not list(app.glob(".mkapp-create-*"))


def test_concurrent_destination_is_not_overwritten(app, monkeypatch):
    original = Path.mkdir
    target = app / "plugins" / "myapp.example"

    def race(self, *args, **kwargs):
        if self == target:
            original(self)
            (self / "sentinel").write_text("other creator")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", race)
    with pytest.raises(ScaffoldError, match="already exists"):
        create_plugin("example", app_root=app)
    assert (target / "sentinel").read_text() == "other creator"
    assert not (target / "mkapp-plugin.toml").exists()


async def test_all_templates_runtime_selection_and_data_wire(app):
    for name, template in (("empty", "empty"), ("viewer", "ui"), ("provider", "data")):
        create_plugin(name, app_root=app, template=template)
    config = AppConfig("test.app", app_root=app, data_root=app / "state")
    items = discover(config)
    assert len(items) == 3
    assert not any(item.package in sys.modules for item in items.values())
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        assert all(entry.status == "ready" for entry in host.inventory.values())
        assert "myapp.viewer.main.v1" in host.windows.registrations
        provider = host.inventory["myapp.provider"].context
        assert provider is not None
        assert await provider.actions.call(
            ActionRef("myapp.provider.sample.v1"), {"count": 3}
        ) == {"values": [100.0, 100.1, 100.2]}
        for payload in ({"count": 0}, {"count": 129}, {"count": True}, {"extra": 1}):
            with pytest.raises(AppError, match="invalid_payload"):
                await provider.actions.call("myapp.provider.sample.v1", payload)
    finally:
        await host.stop()
    assert host.bus.context.closed
    selected = AppConfig(
        "test.app", app_root=app, enabled_plugins=("myapp.provider", "myapp.empty")
    )
    host = Host(selected, WindowHost(Bridge(selected.limits)))
    try:
        await host.start()
        assert host.inventory["myapp.viewer"].status == "disabled"
        assert host.inventory["myapp.provider"].status == "ready"
        assert host.inventory["myapp.empty"].status == "ready"
    finally:
        await host.stop()
    assert (
        resolve(items, AppConfig("test.app", app_root=app, enabled_plugins=())).order
        == ()
    )


@pytest.mark.parametrize(
    "arguments",
    [["--help"], ["plugin", "create", "--help"], ["plugin", "create", "example"]],
)
def test_fresh_process_authoring_stays_headless(app, arguments):
    script = """
import runpy, sys, threading
sys.argv = ['mkapp', *sys.argv[1:]]
try:
    runpy.run_module('mkapp', run_name='__main__')
except SystemExit as error:
    assert error.code == 0
assert 'imgui_bundle' not in sys.modules
assert 'mkapp.runtime.app' not in sys.modules
assert 'zmq' not in sys.modules
assert 'keyring' not in sys.modules
assert len(threading.enumerate()) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script, *arguments],
        cwd=app,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_launcher_arguments_preserved(app, monkeypatch):
    import mkapp.runtime.app as runtime

    calls = []
    monkeypatch.setattr(
        runtime, "run", lambda config, **kwargs: calls.append((config, kwargs))
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mkapp",
            "--app-root",
            str(app),
            "--app-id",
            "test.shell",
            "--safe-mode",
            "--smoke-frames",
            "3",
        ],
    )
    main()
    config, options = calls[0]
    assert config.app_root == app and config.app_id == "test.shell" and config.safe_mode
    assert options == {"smoke_frames": 3}


@pytest.mark.parametrize(
    "arguments,code",
    [(["bad-name"], 2), (["example", "-t", "unknown"], 2), (["example"], 1)],
)
def test_cli_errors(app, monkeypatch, arguments, code, capsys):
    create_plugin("example", app_root=app)
    monkeypatch.chdir(app)
    monkeypatch.setattr(sys, "argv", ["mkapp", "plugin", "create", *arguments])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == code
    assert "Traceback" not in capsys.readouterr().err
