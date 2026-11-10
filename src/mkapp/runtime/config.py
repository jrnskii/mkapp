"""Validated application configuration and atomic, bounded user preferences."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from mkapp.sdk import AppError

ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")
VERSIONED_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\.v[1-9][0-9]*")


def check_id(value: Any, *, versioned: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 160
        or not (VERSIONED_ID if versioned else ID).fullmatch(value)
    ):
        raise AppError("invalid_metadata")
    return value


@dataclass(frozen=True)
class Limits:
    frame_bytes: int = 1_048_576
    depth: int = 16
    items: int = 10_000
    pending: int = 256
    concurrency: int = 64
    gui_queue: int = 1024
    timeout_ms: int = 5000
    lifecycle_seconds: float = 5.0

    def __post_init__(self) -> None:
        ceilings = {
            "frame_bytes": 1_048_576,
            "depth": 16,
            "items": 10_000,
            "pending": 256,
            "concurrency": 64,
            "gui_queue": 1024,
            "timeout_ms": 30_000,
        }
        for name, ceiling in ceilings.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise AppError("invalid_config")
        if not 0 < self.lifecycle_seconds <= 30:
            raise AppError("invalid_config")


@dataclass(frozen=True)
class AppConfig:
    app_id: str
    title: str = "mkapp"
    app_root: Path | None = None
    plugin_roots: tuple[Path, ...] = ()
    approved_distributions: tuple[str, ...] = ()
    enabled_plugins: tuple[str, ...] | None = None
    toggles: Mapping[str, bool] = field(default_factory=dict)
    grants: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    extensions: tuple[str, ...] = ()
    data_root: Path | None = None
    safe_mode: bool = False
    limits: Limits = field(default_factory=Limits)

    def __post_init__(self) -> None:
        check_id(self.app_id)
        if not isinstance(self.limits, Limits) or type(self.safe_mode) is not bool:
            raise AppError("invalid_config")
        if self.app_root is not None and not isinstance(self.app_root, Path):
            raise AppError("invalid_config")
        if self.data_root is not None and not isinstance(self.data_root, Path):
            raise AppError("invalid_config")
        if not isinstance(self.toggles, Mapping) or not isinstance(
            self.grants, Mapping
        ):
            raise AppError("invalid_config")
        object.__setattr__(self, "extensions", tuple(self.extensions))
        object.__setattr__(
            self, "approved_distributions", tuple(self.approved_distributions)
        )
        if not isinstance(self.title, str) or not 1 <= len(self.title) <= 160:
            raise AppError("invalid_config")
        # Optional extensions are rejected until implemented; no pretend availability.
        if (
            set(self.extensions)
            - {
                "state",
                "state_persistence",
                "cache",
                "local_storage",
                "tiles",
                "palette",
                "autocomplete",
                "registry_announcements",
                "plugin_manager",
            }
            or type(self.safe_mode) is not bool
        ):
            raise AppError(
                "feature_unavailable" if self.extensions else "invalid_config"
            )
        root = self.app_root
        if "state_persistence" in self.extensions and "state" not in self.extensions:
            raise AppError("invalid_config")
        if "autocomplete" in self.extensions and "palette" not in self.extensions:
            raise AppError("invalid_config")
        if len(set(self.extensions)) != len(self.extensions):
            raise AppError("invalid_config")
        if (
            root is None
            and sys.argv
            and sys.argv[0]
            and sys.argv[0] not in ("-c", "-m", "")
        ):
            root = Path(sys.argv[0]).resolve().parent
        object.__setattr__(self, "app_root", root.resolve() if root else None)
        roots = tuple(
            (p if p.is_absolute() else (root / p if root else p)).resolve()
            for p in map(Path, self.plugin_roots)
        )
        if root is None and any(not Path(p).is_absolute() for p in self.plugin_roots):
            raise AppError("invalid_config")
        object.__setattr__(self, "plugin_roots", roots)
        if self.enabled_plugins is not None:
            object.__setattr__(
                self,
                "enabled_plugins",
                tuple(check_id(x) for x in self.enabled_plugins),
            )
        for key, value in self.toggles.items():
            check_id(key)
            if type(value) is not bool:
                raise AppError("invalid_config")
        for owner, targets in self.grants.items():
            check_id(owner)
            for target in targets:
                check_id(target, versioned=True)
        object.__setattr__(self, "toggles", MappingProxyType(dict(self.toggles)))
        object.__setattr__(
            self,
            "grants",
            MappingProxyType({k: tuple(v) for k, v in self.grants.items()}),
        )

    @property
    def data_dir(self) -> Path:
        if self.data_root is not None:
            return self.data_root.resolve() / self.app_id
        if sys.platform == "darwin":
            base = Path.home() / "Library/Application Support"
        elif sys.platform == "win32":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        return base / self.app_id


def atomic_json(path: Path, value: Any) -> None:
    from .codec import Codec

    Codec(Limits()).encode(value)  # Reject private/custom/non-finite/oversized values.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mkapp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, allow_nan=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def preferences(
    config: AppConfig, path: Path, *, overrides: Mapping[str, bool] | None = None
) -> AppConfig:
    """User preferences can change desired toggles, never source/security policy."""
    toggles = dict(config.toggles)
    if path.exists():
        try:
            if path.stat().st_size > 65536:
                raise ValueError
            value = json.loads(path.read_text(encoding="utf-8"))
            if (
                type(value) is not dict
                or set(value) != {"schema_version", "toggles"}
                or value["schema_version"] != 1
                or type(value["toggles"]) is not dict
            ):
                raise ValueError
            toggles.update(value["toggles"])
        except ValueError, OSError:
            raise AppError("invalid_config") from None
    toggles.update(overrides or {})
    return replace(config, toggles=toggles)
