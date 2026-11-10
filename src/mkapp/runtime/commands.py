"""Bounded structured command parser. No executable expressions or history."""

from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from typing import Any

from mkapp.sdk import AppError, CommandField, CommandSpec, Registration, ResourceScope

from .config import check_id

TOKEN = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)
VALUE = re.compile(
    r"(?:-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|[a-zA-Z_][a-zA-Z0-9_-]*)",
    re.ASCII,
)


class ParseError(AppError):
    def __init__(self, start: int, end: int) -> None:
        super().__init__("invalid_payload")
        self.start, self.end = start, end


def field_value(spec: CommandField, value: Any) -> Any:
    if value is None and spec.nullable:
        return None
    types = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "enum": str,
    }
    if spec.kind == "number" and type(value) in (int, float) and math.isfinite(value):
        return float(value)
    if (
        spec.kind not in types
        or type(value) is not types[spec.kind]
        or (spec.kind == "enum" and value not in spec.choices)
    ):
        raise ValueError
    return value


class Catalog:
    def __init__(self) -> None:
        self.commands: dict[str, tuple[str, CommandSpec]] = {}
        self.providers: dict[str, str] = {}

    def register(
        self, owner: str, scope: ResourceScope, spec: CommandSpec
    ) -> Registration:
        try:
            check_id(spec.id, versioned=True)
            check_id(spec.action, versioned=True)
            if (
                len(self.commands) >= 256
                or spec.id in self.commands
                or not spec.id.startswith(owner + ".")
                or not 1 <= len(spec.path) <= 8
                or any(not TOKEN.fullmatch(p) for p in spec.path)
                or any(spec.path == s.path for _, s in self.commands.values())
                or len(spec.fields) > 32
            ):
                raise ValueError
            for name, f in spec.fields.items():
                if (
                    not TOKEN.fullmatch(name)
                    or f.kind not in ("string", "integer", "number", "boolean", "enum")
                    or type(f.required) is not bool
                ):
                    raise ValueError
                if not f.required:
                    field_value(f, f.default)
            snapshot = replace(spec, path=tuple(spec.path), fields=dict(spec.fields))
        except Exception:
            raise AppError("invalid_registration") from None
        self.commands[spec.id] = owner, snapshot

        def dispose() -> None:
            self.commands.pop(spec.id, None)

        return scope.own(Registration(dispose))

    def parse(self, text: str) -> tuple[CommandSpec, dict[str, Any]]:
        if type(text) is not str or len(text) > 4096:
            raise ParseError(0, 0)
        matches = []
        for _, spec in self.commands.values():
            pattern = (
                r"\s*" + r"\s+".join(re.escape(p) for p in spec.path) + r"(?=\s|$)"
            )
            if match := re.match(pattern, text):
                matches.append((len(spec.path), match.end(), spec))
        if not matches:
            raise ParseError(0, len(text))
        _, offset, spec = max(matches, key=lambda m: m[0])
        values: dict[str, Any] = {}
        while offset < len(text):
            if not text[offset].isspace():
                raise ParseError(offset, offset + 1)
            while offset < len(text) and text[offset].isspace():
                offset += 1
            if offset == len(text):
                break
            start = offset
            name_match = TOKEN.match(text, offset)
            if name_match is None:
                raise ParseError(start, len(text))
            name, offset = name_match[0], name_match.end()
            if (
                offset >= len(text)
                or text[offset] != "="
                or name not in spec.fields
                or name in values
            ):
                raise ParseError(start, offset)
            offset += 1
            try:
                if offset < len(text) and text[offset] == '"':
                    value, length = json.JSONDecoder().raw_decode(text[offset:])
                    offset += length
                else:
                    match = VALUE.match(text, offset)
                    if match is None:
                        raise ValueError
                    token, offset = match[0], match.end()
                    value = {"true": True, "false": False, "null": None}.get(
                        token, token
                    )
                    if token[0].isdigit() or token[0] == "-":
                        value = json.loads(token)
                if offset < len(text) and not text[offset].isspace():
                    raise ValueError
                values[name] = field_value(spec.fields[name], value)
            except Exception:
                raise ParseError(start, offset) from None
        for name, f in spec.fields.items():
            if name not in values:
                if f.required:
                    raise ParseError(len(text), len(text))
                values[name] = f.default
        return spec, values

    def suggest(self, text: str, cursor: int) -> list[dict[str, Any]]:
        if len(text) > 4096 or not 0 <= cursor <= len(text):
            return []
        prefix = text[:cursor]
        start = len(prefix)
        while start and not prefix[start - 1].isspace():
            start -= 1
        token = prefix[start:]
        suggestions = []
        for _, spec in self.commands.values():
            path = " ".join(spec.path)
            if path.startswith(prefix.lstrip()):
                suggestions.append(
                    {"text": path, "start": 0, "end": cursor, "label": path}
                )
            elif prefix.startswith(path + " "):
                if "=" in token:
                    name, value = token.split("=", 1)
                    f = spec.fields.get(name)
                    if f is not None:
                        candidates = (
                            ("true", "false") if f.kind == "boolean" else f.choices
                        )
                        for candidate in candidates:
                            if candidate.startswith(value):
                                suggestions.append(
                                    {
                                        "text": candidate,
                                        "start": start + len(name) + 1,
                                        "end": cursor,
                                        "label": candidate,
                                    }
                                )
                else:
                    for name in spec.fields:
                        if name.startswith(token):
                            suggestions.append(
                                {
                                    "text": name + "=",
                                    "start": start,
                                    "end": cursor,
                                    "label": name,
                                }
                            )
        unique = {(s["start"], s["end"], s["text"]): s for s in suggestions}
        return [unique[k] for k in sorted(unique)][:20]


class CommandService:
    def __init__(
        self, catalog: Catalog | None, owner: str, scope: ResourceScope
    ) -> None:
        self.catalog, self.owner, self.scope = catalog, owner, scope

    def register(self, spec: CommandSpec) -> Registration:
        if self.catalog is None:
            raise AppError("feature_unavailable")
        return self.catalog.register(self.owner, self.scope, spec)


class CompletionService:
    def __init__(
        self, catalog: Catalog | None, owner: str, scope: ResourceScope
    ) -> None:
        self.catalog, self.owner, self.scope = catalog, owner, scope

    def register(self, action_id: str) -> Registration:
        check_id(action_id, versioned=True)
        if self.catalog is None:
            raise AppError("feature_unavailable")
        if (
            not action_id.startswith(self.owner + ".")
            or action_id in self.catalog.providers
            or len(self.catalog.providers) >= 16
        ):
            raise AppError("invalid_registration")
        self.catalog.providers[action_id] = self.owner

        def dispose() -> None:
            assert self.catalog is not None
            self.catalog.providers.pop(action_id, None)

        return self.scope.own(Registration(dispose))
