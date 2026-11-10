"""Bounded MessagePack primitive subset, shared by every ordinary boundary."""

from __future__ import annotations

import math
from typing import Any

import msgpack

from mkapp.sdk import AppError

from .config import Limits


class Codec:
    def __init__(self, limits: Limits) -> None:
        self.limits = limits

    def validate(self, value: Any) -> None:
        remaining = self.limits.items
        budget = self.limits.frame_bytes

        def visit(item: Any, depth: int) -> None:
            nonlocal remaining, budget
            remaining -= 1
            budget -= 1
            if depth > self.limits.depth or remaining < 0 or budget < 0:
                raise AppError("invalid_payload")
            kind = type(item)
            if kind is int:
                if not -(2**63) <= item < 2**64:
                    raise AppError("invalid_payload")
            elif kind is float:
                if not math.isfinite(item):
                    raise AppError("invalid_payload")
            elif kind in (str, bytes):
                if len(item) > self.limits.frame_bytes:
                    raise AppError("invalid_payload")
                budget -= len(item.encode("utf-8")) if kind is str else len(item)
            elif kind is list:
                for child in item:
                    visit(child, depth + 1)
            elif kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise AppError("invalid_payload")
                    visit(key, depth + 1)
                    visit(child, depth + 1)
            elif item is not None and kind is not bool:
                raise AppError("invalid_payload")
            if budget < 0:
                raise AppError("invalid_payload")

        visit(value, 0)

    def encode(self, value: Any) -> bytes:
        try:
            self.validate(value)
            frame: bytes = msgpack.packb(value, use_bin_type=True, strict_types=True)
            if len(frame) > self.limits.frame_bytes:
                raise ValueError
            return frame
        except ValueError, TypeError, OverflowError, RecursionError:
            raise AppError("invalid_payload") from None

    def decode(self, frame: bytes) -> Any:
        def pairs(items: list[tuple[Any, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if type(key) is not str or key in result:
                    raise ValueError
                result[key] = value
            return result

        def no_extension(code: int, data: bytes) -> Any:
            raise ValueError

        try:
            if len(frame) > self.limits.frame_bytes:
                raise ValueError
            value = msgpack.unpackb(
                frame,
                raw=False,
                strict_map_key=True,
                object_pairs_hook=pairs,
                ext_hook=no_extension,
                max_str_len=self.limits.frame_bytes,
                max_bin_len=self.limits.frame_bytes,
                max_array_len=self.limits.items,
                max_map_len=self.limits.items,
                max_ext_len=0,
            )
            self.validate(value)
            return value
        except Exception:
            raise AppError("invalid_message") from None
