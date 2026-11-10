"""Headless packaged plugin; descriptor discovery must not import this module."""

from mkapp.sdk import Action, Plugin, PluginContext


class Echo(Action):
    id = "example.wheel.echo.v1"
    input = output = staticmethod(lambda value: value)

    async def handle(self, payload, ctx):
        return payload


class WheelPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Echo)


def create_plugin() -> Plugin:
    return WheelPlugin()
