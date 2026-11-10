"""Console parsing; help and authoring never initialize the application runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import AppConfig


def main() -> None:
    """Dispatch authoring commands or launch the existing desktop shell."""
    parser = argparse.ArgumentParser(
        description="Run or extend a trusted mkapp application"
    )
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--app-id", default="local.mkapp")
    parser.add_argument("--safe-mode", action="store_true")
    parser.add_argument("--smoke-frames", type=int)
    commands = parser.add_subparsers(dest="command")
    plugin = commands.add_parser("plugin", help="Create application plugins")
    operations = plugin.add_subparsers(dest="operation", required=True)
    create = operations.add_parser("create", help="Create a plugin under plugins/")
    create.add_argument(
        "plugin_id", help="Relative ID, automatically prefixed by app name"
    )
    create.add_argument(
        "-t", "--template", choices=("empty", "ui", "data"), default="empty"
    )
    create.add_argument("--app-root", type=Path, default=argparse.SUPPRESS)
    create.add_argument(
        "--app-name",
        help="Namespace override (default: project.name or root directory name)",
    )
    create.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "plugin":
        from .scaffold import ScaffoldArgumentError, ScaffoldError, create_plugin

        try:
            result = create_plugin(
                args.plugin_id,
                template=args.template,
                app_root=args.app_root or Path.cwd(),
                app_name=args.app_name,
                dry_run=args.dry_run,
            )
        except ScaffoldArgumentError as error:
            create.error(str(error))
        except ScaffoldError as error:
            parser.exit(1, f"mkapp: {error}\n")
        print(
            f"{'Would create' if args.dry_run else 'Created'} {result.name} at {result}"
        )
        print(f"  mkapp-plugin.toml\n  {result.name.replace('.', '_')}/__init__.py")
        if not args.dry_run:
            print(
                "Restart your application to load the plugin. Explicit allowlists, saved"
            )
            print(
                "toggles, safe mode and source policy still apply; app settings were not changed."
            )
        return

    from .runtime.app import run

    run(
        AppConfig(app_id=args.app_id, app_root=args.app_root, safe_mode=args.safe_mode),
        smoke_frames=args.smoke_frames,
    )
