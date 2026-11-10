"""Window classes register on runtime; all instance operations stay on GUI."""

from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from mkapp.runtime.bus import ActionService
from mkapp.runtime.config import check_id
from mkapp.sdk import AppError, Registration, ResourceScope, Window, WindowContext

from .bridge import Bridge


def immutable(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({k: immutable(v) for k, v in value.items()})
    if type(value) is list:
        return tuple(immutable(v) for v in value)
    return value


@dataclass
class WindowRegistration:
    owner: str
    cls: type[Window]
    title: str
    scope: ResourceScope
    actions: ActionService
    loop: asyncio.AbstractEventLoop
    open_on_start: bool
    active: bool = True


@dataclass
class OpenWindow:
    registration: WindowRegistration
    window: Window
    context: WindowContext
    active: bool = True


class WindowHost:
    def __init__(self, bridge: Bridge, *, tiles: bool = False) -> None:
        self.bridge = bridge
        self.tiles = tiles
        self.root_dock_id = 0
        self.components: Any = None
        self.registrations: dict[str, WindowRegistration] = {}  # Runtime thread only.
        self.windows: dict[str, OpenWindow] = {}  # GUI thread only.

    def register(
        self,
        owner: str,
        cls: type[Window],
        scope: ResourceScope,
        actions: ActionService,
        open_on_start: bool,
    ) -> Registration:
        try:
            if (
                not isinstance(cls, type)
                or not issubclass(cls, Window)
                or cls.render is Window.render
                or inspect.iscoroutinefunction(cls.render)
            ):
                raise ValueError
            identifier = check_id(cls.id, versioned=True)
            if (
                identifier in self.registrations
                or not isinstance(cls.title, str)
                or not 1 <= len(cls.title) <= 160
            ):
                raise ValueError
            inspect.signature(cls).bind()
            if any(
                inspect.iscoroutinefunction(getattr(cls, n))
                for n in ("on_open", "on_close")
            ):
                raise ValueError
        except Exception:
            raise AppError("invalid_registration") from None
        record = WindowRegistration(
            owner,
            cls,
            cls.title,
            scope,
            actions,
            asyncio.get_running_loop(),
            open_on_start,
        )
        self.registrations[identifier] = record

        def dispose() -> None:
            record.active = False
            self.registrations.pop(identifier, None)

        return scope.own(Registration(dispose))

    def ready(self, owner: str) -> None:
        for record in self.registrations.values():
            if record.owner == owner and record.open_on_start:

                def opening(r: WindowRegistration = record) -> None:
                    self.open(r, {})

                self.bridge.post(opening)

    def open(self, record: WindowRegistration, arguments: Any) -> str:
        self.bridge.assert_gui()
        if not record.active or not record.scope.active:
            raise AppError("owner_failed")
        instance_id = uuid.uuid4().hex
        snapshot = immutable(
            self.bridge.codec.decode(self.bridge.codec.encode(arguments))
        )
        instance: OpenWindow | None = None

        def valid() -> bool:
            return bool(
                instance and instance.active and record.active and record.scope.active
            )

        def submit(target: Any, payload: Any, **kwargs: Any) -> Any:
            return self.bridge.submit(
                record.loop, record.actions.call, valid, target, payload, **kwargs
            )

        from .components import ComponentService

        context = WindowContext(
            instance_id,
            snapshot,
            submit,
            ComponentService(self.components, record.owner, record.scope, instance_id),
        )
        try:
            window = record.cls()
            instance = OpenWindow(record, window, context)
            self.windows[instance_id] = instance
            window.on_open(context)
        except Exception:
            self.close(instance_id)
            raise AppError("internal_error") from None
        return instance_id

    def close(self, identifier: str) -> None:
        self.bridge.assert_gui()
        instance = self.windows.pop(identifier, None)
        if instance is None:
            return
        instance.active = False
        try:
            instance.window.on_close(instance.context)
        except Exception:
            pass

    def render(self, imgui: Any) -> None:
        self.bridge.assert_gui()
        if self.tiles:
            self.root_dock_id = imgui.dock_space_over_viewport()
        self.bridge.drain()
        for identifier, instance in list(self.windows.items()):
            if (
                not instance.registration.active
                or not instance.registration.scope.active
            ):
                self.close(identifier)
                continue
            failed = False
            try:
                if not instance.window.visible(instance.context):
                    continue
            except Exception:
                self.close(identifier)
                continue
            flags = 0
            if instance.window.presentation == "bottom_strip":
                viewport = imgui.get_main_viewport()
                imgui.set_next_window_pos(
                    (
                        viewport.work_pos.x,
                        viewport.work_pos.y + viewport.work_size.y - 180,
                    )
                )
                imgui.set_next_window_size((viewport.work_size.x, 180))
                flags = (
                    imgui.WindowFlags_.no_title_bar
                    | imgui.WindowFlags_.no_move
                    | imgui.WindowFlags_.no_resize
                    | imgui.WindowFlags_.no_saved_settings
                    | imgui.WindowFlags_.no_docking
                )
            visible, opened = imgui.begin(
                f"{instance.registration.title}###{identifier}", True, flags
            )
            try:
                if visible:
                    instance.window.render(instance.context)
            except Exception:
                failed = True
            finally:
                imgui.end()
            if failed or not opened:
                self.close(identifier)

    async def request_open(self, payload: Any, ctx: Any) -> Any:
        from mkapp.runtime.records import request

        payload = request(payload, {"factory_id"}, {"arguments", "placement"})
        factory_id = payload["factory_id"]
        if type(factory_id) is not str or factory_id not in self.registrations:
            raise AppError("invalid_payload")
        record = self.registrations[factory_id]
        if (
            record.owner != ctx.source
            and record.owner not in self.components.dependencies.get(ctx.source, set())
        ):
            raise AppError("permission_denied")
        arguments = self.bridge.codec.decode(
            self.bridge.codec.encode(payload.get("arguments", {}))
        )
        placement = payload.get("placement")
        if placement is not None:
            placement = request(placement, {"anchor_window", "side", "ratio"})
            if (
                type(placement["anchor_window"]) is not str
                or placement["side"] not in ("left", "right", "above", "below", "tab")
                or type(placement["ratio"]) not in (int, float)
                or not 0 < placement["ratio"] < 1
            ):
                raise AppError("invalid_payload")
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def opening() -> None:
            if future.done():
                return
            try:
                instance_id = self.open(record, arguments)
                fallback = (
                    self.dock(instance_id, placement) if self.tiles else bool(placement)
                )
                loop.call_soon_threadsafe(
                    finish, {"instance_id": instance_id, "fallback": fallback}, None
                )
            except Exception:
                loop.call_soon_threadsafe(finish, None, AppError("internal_error"))

        def finish(result: Any, error: AppError | None) -> None:
            if not future.done():
                future.set_exception(error) if error else future.set_result(result)

        self.bridge.post(opening)
        return await future

    def dock(self, instance_id: str, placement: Any) -> bool:
        self.bridge.assert_gui()
        from mkapp.sdk import imgui_bundle

        imgui = imgui_bundle.imgui
        internal = imgui.internal
        target = self.root_dock_id
        fallback = False
        anchor = self.windows.get(placement["anchor_window"]) if placement else None
        native = None
        if anchor and anchor.active:
            native = internal.find_window_by_name(
                f"{anchor.registration.title}###{anchor.context.instance_id}"
            )
        if native and native.dock_id:
            target = native.dock_id
        elif placement:
            fallback = True
        if placement and placement["side"] != "tab":
            side = {
                "left": imgui.Dir.left,
                "right": imgui.Dir.right,
                "above": imgui.Dir.up,
                "below": imgui.Dir.down,
            }[placement["side"]]
            split = internal.dock_builder_split_node(
                target, side, min(0.85, max(0.15, placement["ratio"]))
            )
            target = split.id_at_dir
            if anchor is not None and native and not native.dock_id:
                internal.dock_builder_dock_window(
                    f"{anchor.registration.title}###{anchor.context.instance_id}",
                    split.id_at_opposite_dir,
                )
        window = self.windows[instance_id]
        internal.dock_builder_dock_window(
            f"{window.registration.title}###{instance_id}", target
        )
        internal.dock_builder_finish(self.root_dock_id)
        return fallback

    def close_all(self) -> None:
        for identifier in list(self.windows):
            self.close(identifier)


class WindowService:
    def __init__(
        self, host: WindowHost, owner: str, scope: ResourceScope, actions: ActionService
    ) -> None:
        self.host, self.owner, self.scope, self.actions = host, owner, scope, actions

    def register(
        self, cls: type[Window], *, open_on_start: bool = False
    ) -> Registration:
        self.actions.bus.assert_thread()
        return self.host.register(
            self.owner, cls, self.scope, self.actions, open_on_start
        )
