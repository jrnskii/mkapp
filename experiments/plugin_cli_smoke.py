"""Generated starters from an installed wheel; opt into a real floating-window check."""

import argparse
import asyncio
import subprocess
import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from mkapp import AppConfig, run
from mkapp.runtime.host import Host
from mkapp.ui.bridge import Bridge
from mkapp.ui.windows import WindowHost


async def check(config: AppConfig) -> None:
    host = Host(config, WindowHost(Bridge(config.limits)))
    try:
        await host.start()
        assert all(entry.status == "ready" for entry in host.inventory.values())
        context = host.inventory["smokeapp.data"].context
        assert context is not None
        assert await context.actions.call("smokeapp.data.sample.v1", {"count": 2}) == {
            "values": [100.0, 100.1]
        }
    finally:
        await host.stop()
    assert host.bus.context.closed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="mkapp-starters-") as temporary:
        root = Path(temporary)
        for template in ("empty", "ui", "data"):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mkapp",
                    "plugin",
                    "create",
                    template,
                    "-t",
                    template,
                    "--app-root",
                    str(root),
                    "--app-name",
                    "smokeapp",
                ],
                check=True,
                capture_output=True,
            )
        config = AppConfig("smokeapp.check", app_root=root, data_root=root / "state")
        if args.native:
            original = WindowHost.close_all
            observed = []

            def closing(self: WindowHost) -> None:
                assert threading.current_thread() is threading.main_thread()
                observed.extend(w.window.id for w in self.windows.values())
                original(self)

            WindowHost.close_all = closing
            try:
                run(config, smoke_frames=15)
            finally:
                WindowHost.close_all = original
            assert "smokeapp.ui.main.v1" in observed
        else:
            asyncio.run(check(config))
        assert not any(t.name == "mkapp-runtime" for t in threading.enumerate())
    print(
        "Generated starters passed"
        + (" native floating-window check" if args.native else " wheel check")
    )


if __name__ == "__main__":
    main()
