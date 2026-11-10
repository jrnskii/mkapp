"""Synthetic data only. Add/disable the viewer without changing the provider."""

from pathlib import Path

from mkapp import AppConfig, run

if __name__ == "__main__":
    run(
        AppConfig(
            app_id="example.market",
            title="Synthetic market",
            app_root=Path(__file__).parent,
            extensions=("palette", "autocomplete"),
            grants={"example.viewer": ("example.prices.series.v1",)},
        )
    )
