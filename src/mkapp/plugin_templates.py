"""Three bundled one-module starters; plain strings ship inside the mkapp wheel."""

EMPTY = '''"""A minimal trusted local plugin."""

from mkapp.sdk import Plugin


class LocalPlugin(Plugin):
    pass


def create_plugin() -> Plugin:
    return LocalPlugin()
'''

UI = '''"""A floating window; all widget operations run on the GUI thread."""

from mkapp.sdk import Plugin, PluginContext, Window, WindowContext


class MainWindow(Window):
    id = "PLUGIN_ID.main.v1"
    title = "Example"

    def __init__(self) -> None:
        self.count = 0

    def render(self, ctx: WindowContext) -> None:
        from mkapp.sdk import imgui_bundle

        imgui = imgui_bundle.imgui
        imgui.text("Hello from PLUGIN_ID")
        if imgui.button("Increment"):
            self.count += 1
        imgui.text(f"Count: {self.count}")


class LocalPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.windows.register(MainWindow, open_on_start=True)


def create_plugin() -> Plugin:
    return LocalPlugin()
'''

DATA = '''"""A bounded synthetic provider; use managed workers for blocking or CPU work."""

from mkapp.sdk import ActionContext, Plugin, PluginContext, action, contract


@contract
class SampleInput:
    count: int = 32

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 128:
            raise ValueError


@contract
class SampleOutput:
    values: list[float]


@action("PLUGIN_ID.sample.v1")
async def Sample(request: SampleInput, ctx: ActionContext) -> SampleOutput:
    return SampleOutput([100.0 + i / 10 for i in range(request.count)])


class LocalPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Sample)


def create_plugin() -> Plugin:
    return LocalPlugin()
'''

TEMPLATES = {"empty": EMPTY, "ui": UI, "data": DATA}
