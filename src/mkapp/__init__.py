"""Minimal desktop plugin host. Importing mkapp does not initialize native services."""

from collections.abc import Callable

from .runtime.config import AppConfig, Limits

__all__ = ["AppConfig", "Limits", "run", "main"]


def run(
    config: AppConfig,
    *,
    smoke_frames: int | None = None,
    relaunch: Callable[[], None] | None = None,
) -> None:
    from .runtime.app import run as run_app

    run_app(config, smoke_frames=smoke_frames, relaunch=relaunch)


def main() -> None:
    from .cli import main as cli

    cli()
