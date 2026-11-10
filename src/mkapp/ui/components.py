"""Direct reusable render calls, with explicit dependencies and scoped ImGui IDs."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from mkapp.runtime.config import check_id
from mkapp.sdk import AppError, Registration, ResourceScope

from .bridge import Bridge
from .windows import immutable


class ComponentHost:
    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.entries: dict[str, tuple[str, Callable[..., None], ResourceScope]] = {}
        self.dependencies: dict[str, set[str]] = {}


class ComponentService:
    def __init__(
        self,
        host: ComponentHost,
        owner: str,
        scope: ResourceScope,
        instance_id: str | None = None,
    ) -> None:
        self.host, self.owner, self.scope, self.instance_id = (
            host,
            owner,
            scope,
            instance_id,
        )

    def register(self, identifier: str, renderer: Callable[..., None]) -> Registration:
        check_id(identifier, versioned=True)
        if (
            self.instance_id is not None
            or not self.scope.active
            or not identifier.startswith(self.owner + ".")
            or identifier in self.host.entries
            or not callable(renderer)
            or inspect.iscoroutinefunction(renderer)
        ):
            raise AppError("invalid_registration")
        inspect.signature(renderer).bind({}, lambda intent: None)
        self.host.entries[identifier] = self.owner, renderer, self.scope

        def dispose() -> None:
            self.host.entries.pop(identifier, None)

        return self.scope.own(Registration(dispose))

    def render(
        self, identifier: str, instance: str, data: Any, emit: Callable[[Any], None]
    ) -> None:
        self.host.bridge.assert_gui()
        registered = self.host.entries.get(identifier)
        if (
            not self.scope.active
            or self.instance_id is None
            or registered is None
            or not registered[2].active
            or (
                registered[0] != self.owner
                and registered[0] not in self.host.dependencies.get(self.owner, set())
            )
        ):
            raise AppError("permission_denied")
        if type(instance) is not str or not 1 <= len(instance) <= 160:
            raise AppError("invalid_payload")
        from mkapp.sdk import imgui_bundle

        snapshot = immutable(
            self.host.bridge.codec.decode(self.host.bridge.codec.encode(data))
        )
        imgui_bundle.imgui.push_id(f"{self.instance_id}/{identifier}/{instance}")
        try:
            registered[1](snapshot, emit)
        finally:
            imgui_bundle.imgui.pop_id()
