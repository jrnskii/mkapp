"""Compile frozen dataclass adapters once, at registration."""

from __future__ import annotations

import inspect
import types
from dataclasses import MISSING, fields, is_dataclass
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

from mkapp.sdk import Action, ActionContext, AppError

from .codec import Codec

Adapter = Callable[[Any], Any]


def compile_type(schema: Any, stack: tuple[Any, ...] = ()) -> tuple[Adapter, Adapter]:
    if schema in (str, bytes, int, float, bool, type(None)):

        def primitive(value: Any) -> Any:
            if type(value) is not schema:
                raise ValueError
            return value

        return primitive, primitive
    origin, args = get_origin(schema), get_args(schema)
    if origin in (Union, types.UnionType) and len(args) == 2 and type(None) in args:
        other = next(a for a in args if a is not type(None))
        read, write = compile_type(other, stack)
        return (
            lambda v: None if v is None else read(v),
            lambda v: None if v is None else write(v),
        )
    if origin is list and len(args) == 1:
        read, write = compile_type(args[0], stack)

        def sequence(value: Any, convert: Adapter) -> Any:
            if type(value) is not list:
                raise ValueError
            return [convert(x) for x in value]

        return lambda v: sequence(v, read), lambda v: sequence(v, write)
    if origin is dict and len(args) == 2 and args[0] is str:
        read, write = compile_type(args[1], stack)

        def mapping(value: Any, convert: Adapter) -> Any:
            if type(value) is not dict or any(type(k) is not str for k in value):
                raise ValueError
            return {k: convert(v) for k, v in value.items()}

        return lambda v: mapping(v, read), lambda v: mapping(v, write)
    if (
        not isinstance(schema, type)
        or not is_dataclass(schema)
        or not getattr(schema, "__dataclass_params__").frozen
        or schema in stack
    ):
        raise ValueError
    hints = get_type_hints(schema)
    specs = fields(schema)
    if any(not f.init for f in specs) or len(specs) != len(hints):
        raise ValueError
    adapters = {f.name: compile_type(hints[f.name], (*stack, schema)) for f in specs}

    def read_record(value: Any) -> Any:
        if type(value) is not dict or set(value) - adapters.keys():
            raise ValueError
        values = {}
        for f in specs:
            if f.name in value:
                values[f.name] = adapters[f.name][0](value[f.name])
            else:
                default = f.default
                if default is MISSING:
                    if f.default_factory is MISSING:
                        raise ValueError
                    default = f.default_factory()
                # Defaults must satisfy the same schema as supplied fields.
                values[f.name] = adapters[f.name][0](adapters[f.name][1](default))
        return schema(**values)

    def write_record(value: Any) -> dict[str, Any]:
        if type(value) is not schema:
            raise ValueError
        return {name: pair[1](getattr(value, name)) for name, pair in adapters.items()}

    return read_record, write_record


def action_adapters(cls: type[Action], codec: Codec) -> tuple[Adapter, Adapter]:
    try:
        if fn := getattr(cls, "_annotated", None):
            signature = inspect.signature(fn)
            params = list(signature.parameters.values())
            hints = get_type_hints(fn)
            if (
                not inspect.iscoroutinefunction(fn)
                or len(params) != 2
                or any(
                    p.kind not in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                    or p.default is not p.empty
                    for p in params
                )
                or hints.get(params[1].name) is not ActionContext
            ):
                raise ValueError
            request, response = hints[params[0].name], hints["return"]
            if not all(
                isinstance(t, type) and is_dataclass(t) for t in (request, response)
            ):
                raise ValueError
            request_read, request_write = compile_type(request)

            def checked(value: Any) -> Any:
                result = request_read(value)
                codec.encode(request_write(result))
                return result

            return checked, compile_type(response)[1]
        request, response = cls.input, cls.output
        read = (
            compile_type(request)[0]
            if isinstance(request, type) and is_dataclass(request)
            else request
        )
        write = (
            compile_type(response)[1]
            if isinstance(response, type) and is_dataclass(response)
            else response
        )
        if not callable(read) or not callable(write):
            raise ValueError
        return read, write
    except Exception:
        raise AppError("invalid_registration") from None
