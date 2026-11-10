from __future__ import annotations

import sys
from pathlib import Path

import pytest


@pytest.fixture
def plugin_tree(tmp_path: Path):
    """Synthetic source fixtures with unique top-level package names."""
    packages: list[str] = []

    def make(
        name: str,
        source: str = "",
        *,
        requires: dict[str, str] | None = None,
        optional: dict[str, str] | None = None,
        extra: str = "",
    ) -> Path:
        package = (
            "fixture_" + name.replace(".", "_") + "_" + tmp_path.name.replace("-", "_")
        )
        root = tmp_path / "plugins" / name
        (root / package).mkdir(parents=True)
        (root / package / "__init__.py").write_text(
            source
            or "from mkapp.sdk import Plugin\ndef create_plugin(): return Plugin()\n",
            encoding="utf-8",
        )
        text = (
            f'manifest_version = 1\nid = "{name}"\nversion = "0.1.0"\nsdk = ">=0.1,<0.2"\nentry = "{package}:create_plugin"\n'
            + extra
        )
        for key, values in (("requires", requires), ("optional", optional)):
            if values:
                text += f"\n[{key}]\n" + "".join(
                    f'"{k}" = "{v}"\n' for k, v in values.items()
                )
        (root / "mkapp-plugin.toml").write_text(text, encoding="utf-8")
        packages.append(package)
        return root

    yield make
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in packages):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
