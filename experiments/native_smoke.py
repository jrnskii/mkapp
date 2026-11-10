"""Native M0/M3 smoke: synthetic snapshots, lazy export identity and GUI ownership."""

import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from mkapp import AppConfig, run
from mkapp.sdk import imgui_bundle
from mkapp.ui.windows import WindowHost

if __name__ == "__main__":
    import imgui_bundle as upstream

    assert imgui_bundle is upstream
    original = WindowHost.close_all
    original_render = WindowHost.render
    observed = []
    opened = []
    frame = [0]
    builtin_windows = set()
    temp = TemporaryDirectory(prefix="mkapp-native-")

    def render(self, imgui):
        original_render(self, imgui)
        frame[0] += 1
        for candidate in self.windows.values():
            if candidate.window.id == "mkapp.palette.main.v1":
                candidate.window.opened = True
                candidate.window.text = "prices sample count=8"
        if frame[0] in (20, 60) and self.windows:
            item = next(
                w
                for w in self.windows.values()
                if w.window.id == "example.viewer.main.v1"
            )
            item.context.submit(
                "mkapp.windows.open.v1",
                {
                    "factory_id": "example.viewer.main.v1",
                    "arguments": {},
                    "placement": {
                        "anchor_window": item.context.instance_id
                        if frame[0] == 20
                        else "stale",
                        "side": "below",
                        "ratio": 0.4,
                    },
                },
                on_success=opened.append,
                on_error=lambda e: opened.append({"error": e.code}),
            )

    WindowHost.render = render

    def check(self):
        assert threading.current_thread() is threading.main_thread()
        observed.extend(
            bool(getattr(item.window, "prices", [])) for item in self.windows.values()
        )
        builtin_windows.update(item.window.id for item in self.windows.values())
        original(self)

    WindowHost.close_all = check
    run(
        AppConfig(
            app_id="example.smoke",
            title="mkapp synthetic smoke",
            app_root=Path(__file__).resolve().parents[1] / "examples/market",
            grants={
                "example.viewer": ("example.prices.series.v1", "mkapp.windows.open.v1"),
                "mkapp.palette": ("example.prices.series.v1",),
            },
            extensions=(
                "tiles",
                "palette",
                "autocomplete",
                "state",
                "state_persistence",
                "cache",
                "local_storage",
                "registry_announcements",
                "plugin_manager",
            ),
            data_root=Path(temp.name),
        ),
        smoke_frames=120,
    )
    assert any(observed), "GUI did not receive a synthetic action result"
    assert len(opened) == 2 and all("instance_id" in item for item in opened), opened
    assert opened[-1]["fallback"] is True
    assert {"mkapp.palette.main.v1", "mkapp.manager.main.v1"} <= builtin_windows
    temp.cleanup()
    print("Native window, SDK identity, GUI completion and shutdown passed")
