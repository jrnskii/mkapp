"""One trusted plugin owns its actions, workers, private operations and windows."""

from mkapp.sdk import (
    ActionContext,
    AppError,
    CommandField,
    CommandSpec,
    NamespaceSpec,
    Plugin,
    PluginContext,
    SecretValue,
    StateSpec,
    Window,
    WindowContext,
    action,
    contract,
    imgui_bundle,
)

imgui = imgui_bundle.imgui


@contract
class Input:
    count: int = 25_000

    def __post_init__(self):
        if not 0 <= self.count <= 1_000_000:
            raise ValueError


@contract
class Output:
    total: int


@action("example.workbench.calculate.v1")
async def Calculate(request: Input, ctx: ActionContext) -> Output:
    result = await ctx.workers.submit("example.workbench.cpu", {"count": request.count})
    return Output(result["total"])


class WorkbenchWindow(Window):
    id, title = "example.workbench.main.v1", "Synthetic workbench"

    def __init__(self):
        self.result = "Ready"

    def render(self, ctx: WindowContext) -> None:
        if imgui.button("Calculate in spawned process"):
            ctx.submit(Calculate, {}, on_success=self.updated, on_error=self.failed)
        imgui.text(self.result)
        ctx.components.render(
            "example.workbench.badge.v1",
            "one",
            {"label": "Synthetic"},
            lambda intent: None,
        )
        ctx.components.render(
            "example.workbench.badge.v1",
            "two",
            {"label": "Independent instance"},
            lambda intent: None,
        )

    def updated(self, value):
        self.result = str(value["total"])

    def failed(self, error: AppError):
        self.result = error.code


class Workbench(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Calculate)
        ctx.workers.register(
            "example.workbench.cpu",
            "example_workbench.jobs:sum_squares",
            kind="process",
        )
        ctx.workers.register(
            "example.workbench.io", "example_workbench.jobs:synthetic_io", kind="thread"
        )
        ctx.windows.register(WorkbenchWindow, open_on_start=True)
        ctx.components.register(
            "example.workbench.badge.v1", lambda data, emit: imgui.text(data["label"])
        )
        ctx.private.register(
            "example.workbench.length.v1",
            lambda value: {"length": len(value.reveal())},
            safe_result=True,
        )
        if ctx.features.has("palette"):
            ctx.commands.register(
                CommandSpec(
                    "example.workbench.command.v1",
                    ("workbench", "calculate"),
                    Calculate.id,
                    {"count": CommandField("integer", required=False, default=25_000)},
                )
            )
        if ctx.features.has("state"):
            ctx.state.register(
                StateSpec(
                    "example.workbench.runs",
                    lambda v: v,
                    persist=ctx.features.has("state_persistence"),
                )
            )
        if ctx.features.has("cache"):
            ctx.cache.register(NamespaceSpec("example.workbench.cache", lambda v: v))
        if ctx.features.has("local_storage"):
            ctx.local_storage.register(
                NamespaceSpec("example.workbench.presets", lambda v: v)
            )

    async def start(self, ctx: PluginContext) -> None:
        handle = ctx.private.hold(SecretValue("SYNTHETIC-PRIVATE-INPUT"))
        safe = await ctx.private.invoke("example.workbench.length.v1", handle)
        assert safe == {"length": 23}
        await ctx.workers.submit("example.workbench.io", {})
        if ctx.features.has("state"):
            old = await ctx.state.get("example.workbench.runs")
            await ctx.state.set(
                "example.workbench.runs",
                (old["value"] or 0) + 1,
                expected_revision=old["revision"],
            )
        if ctx.features.has("cache"):
            await ctx.cache.put("example.workbench.cache", "nullable", None)
        if ctx.features.has("local_storage"):
            await ctx.local_storage.put(
                "example.workbench.presets", "demo", {"label": "Synthetic preset"}
            )


def create_plugin() -> Plugin:
    return Workbench()
