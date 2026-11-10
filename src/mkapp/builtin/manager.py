"""Private inventory UI, composed entirely through SDK registrations and actions."""

import time
from typing import Any

from mkapp.sdk import Plugin, PluginContext, Window, WindowContext


class ManagerPlugin(Plugin):
    def __init__(self, operations: dict[str, Any]) -> None:
        self.operations = operations

    def prepare(self, ctx: PluginContext) -> None:
        from .actions import ActionsPlugin

        # Composition, not another plugin base hierarchy or hidden registration API.
        ActionsPlugin(self.operations).prepare(ctx)

        class Manager(Window):
            id = "mkapp.manager.main.v1"
            title = "Plugins"

            def __init__(self) -> None:
                self.snapshot: dict[str, Any] = {"plugins": []}
                self.proposal: dict[str, Any] | None = None
                self.last = 0.0
                self.error = ""

            def render(self, ctx: WindowContext) -> None:
                from mkapp.sdk import imgui_bundle

                imgui = imgui_bundle.imgui
                if time.monotonic() - self.last > 0.5:
                    self.last = time.monotonic()
                    ctx.submit(
                        "mkapp.manager.snapshot.v1",
                        {},
                        on_success=lambda v: setattr(self, "snapshot", v),
                        on_error=lambda e: setattr(self, "error", e.code),
                    )
                for item in self.snapshot["plugins"]:
                    changed, desired = imgui.checkbox(item["id"], item["desired"])
                    imgui.same_line()
                    imgui.text(
                        item["status"]
                        + (" (restart required)" if item["pending_restart"] else "")
                    )
                    if changed:
                        ctx.submit(
                            "mkapp.manager.propose.v1",
                            {"toggles": {item["id"]: desired}},
                            on_success=lambda v: setattr(self, "proposal", v),
                            on_error=lambda e: setattr(self, "error", e.code),
                        )
                if self.proposal is not None:
                    for key, value in self.proposal["toggles"].items():
                        imgui.text(
                            f"{key}: {'enabled' if value else 'disabled'} after restart"
                        )
                    if imgui.button("Save proposal"):
                        ctx.submit(
                            "mkapp.manager.commit.v1",
                            {"toggles": self.proposal["toggles"]},
                            on_success=lambda v: setattr(self, "proposal", None),
                            on_error=lambda e: setattr(self, "error", e.code),
                        )
                if self.snapshot.get("can_restart") and imgui.button("Restart"):
                    ctx.submit(
                        "mkapp.manager.restart.v1",
                        {},
                        on_success=lambda v: None,
                        on_error=lambda e: setattr(self, "error", e.code),
                    )
                if self.error:
                    imgui.text(self.error)

        ctx.windows.register(Manager, open_on_start=True)
