# mkapp

A Python desktop host for trusted local plugins, using ImGui-Bundle, an asyncio runtime thread, and a ZeroMQ/MessagePack action bus.

**Implementation is in progress.** The examples run, but the full delivery plan and release acceptance are not complete. See [implementation evidence and limitations](IMPLEMENTATION.md).

Requires CPython 3.14. The tested native target is macOS 27.0 on Apple Silicon.

```sh
uv sync --dev
uv run python examples/market/app.py
```

The market example discovers a headless synthetic-price provider and an independent floating viewer. The provider stays unchanged when the viewer is added or disabled. All optional framework extensions are off in this example.

```sh
uv run python examples/workbench/app.py
uv run --extra duckdb python examples/workbench/app.py --storage
```

The workbench demonstrates explicit process/thread workers, private operations, reusable render components, the palette, state and cache. Press Cmd+P on macOS or Ctrl+P elsewhere, then enter `workbench calculate count=1000`. Its records and private inputs are synthetic.

An empty shell, or a configured application root:

```sh
uv run mkapp --safe-mode
uv run mkapp --app-root examples/market
```

Use an application script to configure cross-plugin action grants, as the examples do. The CLI starts with no cross-plugin grants.

Plugins use `mkapp.sdk`, inherit `Plugin`, and register `Action` and `Window` classes explicitly in `prepare`. Directory plugins contain `mkapp-plugin.toml` and a uniquely named Python package. Installed plugins require explicit distribution approval. Changes take effect on restart.

Create an app-local starter with `mkapp plugin create example -t ui`. Templates are `empty` (default), `ui`, and `data`; `--template` also works. The app name automatically prefixes the relative ID, so a project named `myapp` creates `myapp.example`. See [generator usage and naming](EXAMPLES.md#create-a-plugin).

```python
from pathlib import Path
from mkapp import AppConfig, run

if __name__ == "__main__":
    run(AppConfig(
        app_id="example.my_app",
        app_root=Path(__file__).parent,
        # Omit enabled_plugins for all eligible plugins; () selects none.
        extensions=(),
    ))
```

All GUI operations stay on the main thread. Render callbacks submit actions and receive results at frame boundaries. Action handlers run on the runtime thread; blocking or CPU work uses explicit named workers. All imported plugins are trusted Python code, and the SDK is not a security sandbox.

The base dependencies have distinct roles: ImGui-Bundle supplies the native GUI, PyZMQ the transport, MessagePack the bounded wire codec, `packaging` plugin version rules, and `keyring` the native credential adapter. DuckDB is confined to the optional `duckdb` extra. No validation or plugin-framework dependency is required.

Verification:

```sh
uv run --extra duckdb pytest -q
uv run mypy
uv run ruff check src tests examples experiments
uv run ruff format --check src tests examples experiments
uv run --extra duckdb python experiments/native_smoke.py
```

The native smoke opens real windows and closes automatically. The isolated wheel check and native credential probe are documented in [IMPLEMENTATION.md](IMPLEMENTATION.md). The credential probe writes and deletes only a uniquely named synthetic credential; it is not part of ordinary tests.

See [runnable examples and SDK usage](EXAMPLES.md), [architecture](ARCHITECTURE.md), and [delivery plan](PLAN.md).

For LLM-assisted plugin development, start with the [LLM SDK guide](docs/LLM_SDK_GUIDE.md). [llms.txt](llms.txt) provides a compact documentation entry point.
