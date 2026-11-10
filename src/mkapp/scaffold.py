"""Offline, metadata-only creation of trusted app-local plugins."""

from __future__ import annotations

import ast
import keyword
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

from . import AppConfig
from .plugin_templates import TEMPLATES
from .runtime.config import check_id
from .runtime.discovery import descriptor, discover
from .sdk import AppError

MANIFEST_VERSION = 1
SDK_RANGE = ">=0.1,<0.2"
NAME = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")


class ScaffoldError(Exception):
    """A safe, fixed authoring error suitable for console output."""


class ScaffoldArgumentError(ScaffoldError):
    """Invalid author-supplied names or template selections."""


def application_name(root: Path, override: str | None) -> str:
    """Read project metadata without importing or executing application code."""
    if override is not None:
        name = override
    else:
        metadata = root / "pyproject.toml"
        name = root.name
        if metadata.exists():
            if metadata.stat().st_size > 65536:
                raise ScaffoldError("Project metadata is too large; use --app-name.")
            project = tomllib.loads(metadata.read_text(encoding="utf-8")).get(
                "project", {}
            )
            name = project.get("name", name)
        if isinstance(name, str):
            name = name.lower().replace("-", "_")
    if (
        not isinstance(name, str)
        or not NAME.fullmatch(name)
        or name.split(".")[0] == "mkapp"
    ):
        raise ScaffoldArgumentError("Invalid or reserved app name; specify --app-name.")
    return name


def create_plugin(
    plugin_id: str,
    *,
    template: str = "empty",
    app_root: Path,
    app_name: str | None = None,
    dry_run: bool = False,
) -> Path:
    """Create files synchronously without loading any plugins or runtime services."""
    try:
        root = app_root.resolve(strict=True)
        if not root.is_dir():
            raise ScaffoldError("Application root must be an existing directory.")
        if template not in TEMPLATES:
            raise ScaffoldArgumentError("Unknown template; choose empty, ui or data.")
        if not NAME.fullmatch(plugin_id):
            raise ScaffoldArgumentError(
                "Plugin ID must contain lowercase identifier segments."
            )
        identifier = f"{application_name(root, app_name)}.{plugin_id}"
        try:
            check_id(identifier)
            check_id(f"{identifier}.sample.v1", versioned=True)
        except AppError:
            raise ScaffoldArgumentError(
                "Prefixed plugin ID is invalid or too long."
            ) from None
        package = identifier.replace(".", "_")
        if (
            not package.isidentifier()
            or keyword.iskeyword(package)
            or package in (sys.stdlib_module_names | {"mkapp"})
        ):
            raise ScaffoldArgumentError(
                "Generated package name is reserved or invalid."
            )
        folder = root / "plugins"
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise ScaffoldError(
                "plugins must be an application-local directory, not a symlink."
            )
        target = folder / identifier
        if target.exists() or target.is_symlink():
            raise ScaffoldError(
                "Plugin destination already exists; nothing was overwritten."
            )
        items = discover(AppConfig("authoring.app", app_root=root))
        if any(
            item.id == identifier or item.package == package for item in items.values()
        ):
            raise ScaffoldError(
                "Plugin ID or Python package conflicts with an existing plugin."
            )

        source = TEMPLATES[template].replace("PLUGIN_ID", identifier)
        ast.parse(source)
        manifest = (
            f'manifest_version = {MANIFEST_VERSION}\nid = "{identifier}"\n'
            f'version = "0.1.0"\nsdk = "{SDK_RANGE}"\n'
            f'entry = "{package}:create_plugin"\nannounce = false\n'
            "required_features = []\n"
        )
        if dry_run:
            return target
        # Stage outside plugins/ so discovery cannot see the staging descriptor.
        with tempfile.TemporaryDirectory(
            prefix=".mkapp-create-", dir=root
        ) as temporary:
            stage = Path(temporary)
            (stage / package).mkdir()
            (stage / package / "__init__.py").write_text(source, encoding="utf-8")
            metadata = stage / "mkapp-plugin.toml"
            metadata.write_text(manifest, encoding="utf-8")
            descriptor(metadata, stage)
            folder.mkdir(exist_ok=True)
            if folder.is_symlink() or folder.resolve() != folder:
                raise ScaffoldError("plugins directory changed during generation.")
            target.mkdir()  # Exclusive reservation: never replace a concurrent creator.
            try:
                (stage / package).rename(target / package)
                # Publish the descriptor last, atomically and without replacement.
                os.link(metadata, target / metadata.name)
            except BaseException:
                # Remove only our known files; never recursively delete plugin directories.
                (target / package / "__init__.py").unlink(missing_ok=True)
                if (target / package).is_dir():
                    (target / package).rmdir()
                target.rmdir()
                raise
        return target
    except ScaffoldError:
        raise
    except FileExistsError:
        raise ScaffoldError(
            "Plugin destination already exists; nothing was overwritten."
        ) from None
    except OSError, ValueError, TypeError, AttributeError, AppError:
        raise ScaffoldError(
            "Could not create plugin; check app metadata, paths and permissions."
        ) from None
