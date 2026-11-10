"""Independent consumer. Uses the provider's public ID, never its implementation."""

from mkapp.sdk import (
    AppError,
    CommandField,
    CommandSpec,
    Plugin,
    PluginContext,
    Window,
    WindowContext,
    imgui_bundle,
)

imgui = imgui_bundle.imgui


class PricesWindow(Window):
    id = "example.viewer.main.v1"
    title = "Synthetic prices"

    def __init__(self) -> None:
        self.prices: list[float] = []
        self.status = "Ready"

    def on_open(self, ctx: WindowContext) -> None:
        self.refresh(ctx)

    def refresh(self, ctx: WindowContext) -> None:
        self.status = "Loading"
        ctx.submit(
            "example.prices.series.v1",
            {"count": 32},
            on_success=self.updated,
            on_error=self.failed,
        )

    def updated(self, result: dict) -> None:
        self.prices = result["prices"]
        self.status = "Loaded"

    def failed(self, error: AppError) -> None:
        self.status = error.code

    def render(self, ctx: WindowContext) -> None:
        imgui.text(self.status)
        ctx.components.render(
            "example.viewer.summary.v1",
            "first",
            {"count": len(self.prices)},
            lambda intent: None,
        )
        ctx.components.render(
            "example.viewer.summary.v1",
            "second",
            {"count": len(self.prices)},
            lambda intent: None,
        )
        if imgui.button("Refresh"):
            self.refresh(ctx)
        for index, price in enumerate(self.prices):
            imgui.text(f"{index:02d}: {price:.2f}")


class Viewer(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.components.register(
            "example.viewer.summary.v1",
            lambda data, emit: imgui.text(f"Samples: {data['count']}"),
        )
        ctx.windows.register(PricesWindow, open_on_start=True)
        if ctx.features.has("palette"):
            ctx.commands.register(
                CommandSpec(
                    "example.viewer.sample.v1",
                    ("prices", "sample"),
                    "example.prices.series.v1",
                    {"count": CommandField("integer", required=False, default=32)},
                )
            )


def create_plugin() -> Plugin:
    return Viewer()
