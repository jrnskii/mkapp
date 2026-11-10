"""A one-module headless provider; no GUI imports."""

from mkapp.sdk import ActionContext, Plugin, PluginContext, action, contract


@contract
class SeriesInput:
    count: int = 32

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 128:
            raise ValueError


@contract
class SeriesOutput:
    prices: list[float]


@action("example.prices.series.v1")
async def Series(request: SeriesInput, ctx: ActionContext) -> SeriesOutput:
    return SeriesOutput([100.0 + ((i * 7) % 17) / 10 for i in range(request.count)])


class Prices(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Series)


def create_plugin() -> Plugin:
    return Prices()
