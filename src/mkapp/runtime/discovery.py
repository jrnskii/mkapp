"""Static discovery: approved sources only, no entry point imports."""

from __future__ import annotations

import importlib.metadata
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from mkapp.sdk import AppError

from .config import AppConfig, check_id

ENTRY = re.compile(r"([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*):([a-zA-Z_]\w*)", re.ASCII)


@dataclass(frozen=True)
class Descriptor:
    id: str
    version: Version
    entry: str
    root: Path
    package: str
    requires: dict[str, SpecifierSet]
    optional: dict[str, SpecifierSet]
    required_features: tuple[str, ...]
    announce: bool
    distribution: str | None = None


def descriptor(path: Path, root: Path, distribution: str | None = None) -> Descriptor:
    try:
        if (
            not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > 65536
        ):
            raise ValueError
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        allowed = {
            "manifest_version",
            "id",
            "version",
            "sdk",
            "entry",
            "announce",
            "required_features",
            "requires",
            "optional",
        }
        if (
            set(data) - allowed
            or type(data["manifest_version"]) is not int
            or data["manifest_version"] != 1
        ):
            raise ValueError
        identifier = check_id(data["id"])
        if identifier.startswith("mkapp."):
            raise ValueError
        if Version("0.1.0") not in SpecifierSet(data["sdk"]):
            raise ValueError
        match = ENTRY.fullmatch(data["entry"])
        if match is None:
            raise ValueError
        package = match[1].split(".")[0]
        package_root = root / package
        if (
            package == "mkapp"
            or not package_root.is_dir()
            or not (package_root / "__init__.py").is_file()
        ):
            raise ValueError
        # Inspect only the entry path, never recursively scan plugin trees.
        entry_path = root.joinpath(*match[1].split("."))
        for child in (
            package_root,
            entry_path.with_suffix(".py"),
            entry_path / "__init__.py",
        ):
            if child.exists() and not child.resolve().is_relative_to(root.resolve()):
                raise ValueError
        required_features = data.get("required_features", [])
        if type(required_features) is not list or any(
            type(x) is not str for x in required_features
        ):
            raise ValueError
        announce = data.get("announce", False)
        if type(announce) is not bool:
            raise ValueError

        def dependencies(name: str) -> dict[str, SpecifierSet]:
            value: Any = data.get(name, {})
            if type(value) is not dict:
                raise ValueError
            return {check_id(k): SpecifierSet(v) for k, v in value.items()}

        return Descriptor(
            identifier,
            Version(data["version"]),
            data["entry"],
            root.resolve(),
            package,
            dependencies("requires"),
            dependencies("optional"),
            tuple(required_features),
            announce,
            distribution,
        )
    except Exception:
        raise AppError("invalid_metadata") from None


def discover(config: AppConfig) -> dict[str, Descriptor]:
    candidates: list[Descriptor] = []
    if config.app_root:
        folder = config.app_root / "plugins"
        if folder.exists():
            if not folder.resolve().is_relative_to(config.app_root):
                raise AppError("unapproved_source")
            for child in sorted(folder.iterdir()):
                if child.is_dir() and (child / "mkapp-plugin.toml").is_file():
                    if not child.resolve().is_relative_to(folder.resolve()):
                        raise AppError("unapproved_source")
                    candidates.append(descriptor(child / "mkapp-plugin.toml", child))
    for root in config.plugin_roots:
        candidates.append(descriptor(root / "mkapp-plugin.toml", root))
    for name in config.approved_distributions:
        try:
            dist = importlib.metadata.distribution(name)
            entries = [e for e in dist.entry_points if e.group == "mkapp.plugins"]
            if len(entries) != 1:
                raise ValueError
            entry = entries[0]
            match = ENTRY.fullmatch(entry.value)
            if match is None:
                raise ValueError
            package = match[1].split(".")[0]
            relative = f"{package}/mkapp-plugin.toml"
            files = {str(p) for p in dist.files or ()}
            module_file = match[1].replace(".", "/")
            if relative not in files or not (
                {module_file + ".py", module_file + "/__init__.py"} & files
            ):
                raise ValueError
            path = Path(str(dist.locate_file(relative)))
            item = descriptor(path, path.parent.parent, name)
            if item.id != entry.name or item.entry != entry.value:
                raise ValueError
            candidates.append(item)
        except Exception:
            raise AppError("invalid_metadata") from None
    result: dict[str, Descriptor] = {}
    if len(candidates) > 128:
        raise AppError("invalid_metadata")
    packages: set[str] = set()
    for item in sorted(candidates, key=lambda d: d.id):
        if item.id in result or item.package in packages:
            raise AppError("duplicate_plugin")
        result[item.id] = item
        packages.add(item.package)
    return result


@dataclass(frozen=True)
class Resolution:
    order: tuple[str, ...]
    desired: frozenset[str]
    blocked: dict[str, str]


def resolve(items: dict[str, Descriptor], config: AppConfig) -> Resolution:
    desired = (
        set(items) if config.enabled_plugins is None else set(config.enabled_plugins)
    )
    desired = {k for k in desired if config.toggles.get(k, True)}
    if config.safe_mode:
        desired.clear()
    blocked = {k: "missing_plugin" for k in desired if k not in items}
    for k in desired & items.keys():
        item = items[k]
        if set(item.required_features) - set(config.extensions):
            blocked[k] = "feature_unavailable"
        for dep, versions in item.requires.items():
            if dep not in desired or dep not in items:
                blocked[k] = "missing_dependency"
            elif items[dep].version not in versions:
                blocked[k] = "incompatible_dependency"
    visiting: list[str] = []
    done: set[str] = set()
    order: list[str] = []

    def visit(k: str) -> None:
        if k in visiting:
            raise AppError("dependency_cycle")
        if k in done or k not in items:
            return
        visiting.append(k)
        item = items[k]
        edges = set(item.requires) & desired
        edges.update(
            dep
            for dep, versions in item.optional.items()
            if dep in desired and dep in items and items[dep].version in versions
        )
        for dep in sorted(edges):
            visit(dep)
        visiting.pop()
        done.add(k)
        if any(dep in blocked for dep in item.requires):
            blocked[k] = "blocked_dependency"
        if k not in blocked:
            order.append(k)

    for k in sorted(desired):
        visit(k)
    return Resolution(tuple(order), frozenset(desired), blocked)
