"""Optional command strip. Parser/catalog are application bootstrap providers."""

from __future__ import annotations

import sys
import time
from typing import Any

from mkapp.sdk import AppError, Plugin, PluginContext, Window, WindowContext


class PalettePlugin(Plugin):
    def __init__(self, catalog: Any, autocomplete: bool) -> None:
        self.catalog, self.autocomplete = catalog, autocomplete

    def prepare(self, ctx: PluginContext) -> None:
        catalog, autocomplete = self.catalog, self.autocomplete

        class Palette(Window):
            id = "mkapp.palette.main.v1"
            title = "Commands"
            presentation = "bottom_strip"

            def __init__(self) -> None:
                self.opened = self.focus = False
                self.text = self.error = ""
                self.cursor = self.selected = 0
                self.suggestions: list[dict[str, Any]] = []
                self.changed_at = 0.0
                self.query: tuple[str, int] | None = None
                self.requests: list[Any] = []

            def visible(self, ctx: WindowContext) -> bool:
                from mkapp.sdk import imgui_bundle

                imgui = imgui_bundle.imgui
                io = imgui.get_io()
                modal = imgui.is_popup_open(
                    "",
                    imgui.PopupFlags_.any_popup_id | imgui.PopupFlags_.any_popup_level,
                )
                modifier = io.key_super if sys.platform == "darwin" else io.key_ctrl
                if not modal and modifier and imgui.is_key_pressed(imgui.Key.p, False):
                    self.opened = not self.opened
                    self.focus = self.opened
                if self.opened and imgui.is_key_pressed(imgui.Key.escape, False):
                    if self.suggestions:
                        self.suggestions.clear()
                    else:
                        self.opened = False
                return self.opened

            def render(self, ctx: WindowContext) -> None:
                from mkapp.sdk import imgui_bundle

                imgui = imgui_bundle.imgui
                if self.focus:
                    imgui.set_keyboard_focus_here()
                    self.focus = False

                def cursor(data: Any) -> int:
                    if (
                        autocomplete
                        and data.event_flag == imgui.InputTextFlags_.callback_completion
                        and self.suggestions
                    ):
                        item = self.suggestions[self.selected % len(self.suggestions)]
                        buffer = data.buf
                        start = len(buffer[: item["start"]].encode("utf-8"))
                        end = len(buffer[: item["end"]].encode("utf-8"))
                        data.delete_chars(start, end - start)
                        data.insert_chars(start, item["text"])
                        data.cursor_pos = start + len(item["text"].encode("utf-8"))
                        data.selection_start = data.selection_end = data.cursor_pos
                        self.suggestions.clear()
                    if (
                        autocomplete
                        and data.event_flag == imgui.InputTextFlags_.callback_history
                        and self.suggestions
                    ):
                        delta = -1 if data.event_key == imgui.Key.up_arrow else 1
                        self.selected = (self.selected + delta) % len(self.suggestions)
                    self.cursor = len(
                        data.buf.encode("utf-8")[: data.cursor_pos].decode(
                            "utf-8", errors="ignore"
                        )
                    )
                    return 0

                changed, self.text = imgui.input_text(
                    "##command",
                    self.text,
                    imgui.InputTextFlags_.callback_always
                    | (
                        imgui.InputTextFlags_.callback_completion
                        | imgui.InputTextFlags_.callback_history
                        if autocomplete
                        else 0
                    ),
                    cursor,
                )
                if changed:
                    self.changed_at = time.monotonic()
                    for handle in self.requests:
                        handle.cancel()
                    self.requests.clear()
                    self.suggestions = (
                        catalog.suggest(self.text, self.cursor) if autocomplete else []
                    )
                    self.selected = 0
                query = (self.text, self.cursor)
                if (
                    autocomplete
                    and time.monotonic() - self.changed_at > 0.1
                    and self.query != query
                ):
                    self.query = query
                    for target in catalog.providers:

                        def received(result: Any, q: tuple[str, int] = query) -> None:
                            if (
                                q != (self.text, self.cursor)
                                or type(result) is not list
                            ):
                                return
                            for item in result[:20]:
                                if (
                                    type(item) is dict
                                    and set(item) == {"text", "start", "end", "label"}
                                    and type(item["start"]) is int
                                    and type(item["end"]) is int
                                    and 0
                                    <= item["start"]
                                    <= item["end"]
                                    <= len(self.text)
                                    and type(item["text"]) is str
                                    and len(item["text"]) <= 256
                                    and type(item["label"]) is str
                                ):
                                    self.suggestions.append(item)
                            unique = {
                                (s["start"], s["end"], s["text"]): s
                                for s in self.suggestions
                            }
                            self.suggestions = [unique[k] for k in sorted(unique)][:20]

                        self.requests.append(
                            ctx.submit(
                                target,
                                {"text": self.text, "cursor": self.cursor},
                                on_success=received,
                                on_error=lambda e: None,
                                timeout_ms=250,
                            )
                        )
                if imgui.is_key_pressed(imgui.Key.enter, False):
                    try:
                        spec, payload = catalog.parse(self.text)
                        ctx.submit(
                            spec.action,
                            payload,
                            on_success=lambda _: setattr(self, "error", ""),
                            on_error=lambda e: setattr(self, "error", e.code),
                        )
                    except AppError as error:
                        self.error = error.code
                if self.error:
                    imgui.text(self.error)
                for item in self.suggestions[:5]:
                    imgui.text(item["label"])

        ctx.windows.register(Palette, open_on_start=True)
