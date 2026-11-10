"""Bootstrap-injected operations registered as ordinary SDK Action classes."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mkapp.sdk import Action, ActionContext, Plugin, PluginContext


class ActionsPlugin(Plugin):
    def __init__(
        self, operations: Mapping[str, Callable[[Any, ActionContext], Awaitable[Any]]]
    ) -> None:
        self.operations = operations
        self.events: dict[str, Callable[[Any], Any]] = {}

    def prepare(self, ctx: PluginContext) -> None:
        for identifier, validator in self.events.items():
            ctx.events.register(identifier, validator)
        for identifier, callback in self.operations.items():

            async def handle(
                self: Action,
                payload: Any,
                action_ctx: ActionContext,
                fn: Callable[..., Any] = callback,
            ) -> Any:
                return await fn(payload, action_ctx)

            cls = type(
                "BuiltinAction",
                (Action,),
                {
                    "id": identifier,
                    "input": staticmethod(lambda v: v),
                    "output": staticmethod(lambda v: v),
                    "handle": handle,
                },
            )
            ctx.actions.register(cls)
