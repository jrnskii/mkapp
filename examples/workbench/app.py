"""Synthetic workers/private/state/cache demo. Pass --storage with mkapp[duckdb]."""

import argparse
from pathlib import Path

from mkapp import AppConfig, run

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", action="store_true")
    args = parser.parse_args()
    extensions = ("palette", "autocomplete", "state", "state_persistence", "cache")
    if args.storage:
        extensions += ("local_storage",)
    run(
        AppConfig(
            "example.workbench",
            title="Synthetic workbench",
            app_root=Path(__file__).parent,
            extensions=extensions,
            grants={"mkapp.palette": ("example.workbench.calculate.v1",)},
        )
    )
