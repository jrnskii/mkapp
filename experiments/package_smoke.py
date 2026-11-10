"""Run with the installed host and fixture wheels in a clean environment."""

import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mkapp import AppConfig
from mkapp.runtime.discovery import discover
from mkapp.runtime.host import Host
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


async def check():
    with TemporaryDirectory(prefix="mkapp-wheel-") as tmp:
        config = AppConfig(
            "example.packaging",
            app_root=Path(tmp),
            data_root=Path(tmp),
            approved_distributions=("mkapp-example-wheel",),
        )
        assert set(discover(config)) == {"example.wheel"}
        assert "mkapp_example_wheel" not in sys.modules
        host = Host(config, WindowHost(Bridge(config.limits)))
        try:
            await host.start()
            entry = host.inventory["example.wheel"]
            assert entry.status == "ready", entry.error
            assert await entry.context.actions.call(
                "example.wheel.echo.v1", {"synthetic": 42}
            ) == {"synthetic": 42}
            assert "imgui_bundle" not in sys.modules
            assert "duckdb" not in sys.modules
        finally:
            await host.stop()
        assert not host.shutdown_errors
        assert host.bus.context.closed
        print("Clean wheel discovery, headless action and shutdown passed")


if __name__ == "__main__":
    asyncio.run(check())
