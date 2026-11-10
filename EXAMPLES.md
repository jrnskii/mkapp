# Runnable examples and SDK usage

For a comprehensive current API reference and agent instructions, see the [LLM SDK guide](docs/LLM_SDK_GUIDE.md).

The examples below use the current implementation. [IMPLEMENTATION.md](IMPLEMENTATION.md) lists acceptance gaps. The original design sketches are preserved in [docs/DESIGN_EXAMPLES.md](docs/DESIGN_EXAMPLES.md); those sketches are not executable API documentation.

## Run the applications

```sh
uv run python examples/market/app.py
uv run python examples/workbench/app.py
uv run --extra duckdb python examples/workbench/app.py --storage
```

[Market](examples/market/app.py) contains a one-module annotated price provider and an independent viewer. It uses floating windows with all optional built-ins off. [Workbench](examples/workbench/app.py) demonstrates process/thread work, private operations, components, commands, state and cache. Storage is enabled only with `--storage` and the DuckDB extra.

Both applications contain synthetic data only. Workbench persists a synthetic run counter and, with storage enabled, a synthetic preset under its per-app data directory.

## Application and descriptor

```python
from pathlib import Path
from mkapp import AppConfig, run

if __name__ == "__main__":
    run(AppConfig(
        app_id="example.desktop",
        app_root=Path(__file__).parent,
        grants={"example.viewer": ("example.prices.series.v1",)},
    ))
```

The default directory is `plugins/` next to the application script, rather than the current working directory. Set `app_root` explicitly when embedding. Omitting `enabled_plugins` selects all eligible plugins; an explicit tuple is an allowlist, and `()` selects none. Saved toggles stay within that selection. `safe_mode=True` imports no application plugins.

Each immediate child plugin directory contains a descriptor and a uniquely named Python package:

```toml
manifest_version = 1
id = "example.prices"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "synthetic_prices:create_plugin"
announce = false
```

The factory returns a `mkapp.sdk.Plugin`. Add `[requires]` with namespaced plugin IDs and packaging version specifiers for mandatory dependencies. Static metadata is validated before imports. Dependency resolution never installs packages or widens the allowlist.

The [wheel example](examples/wheel/pyproject.toml) includes the same descriptor under its entry package and advertises `mkapp.plugins`. An application approves it with `approved_distributions=("mkapp-example-wheel",)`. See the isolated install procedure in the acceptance record.

## Create a plugin

From an application directory named `myapp`, create a starter without editing framework source:

```sh
mkapp plugin create example
mkapp plugin create dashboard -t ui
mkapp plugin create prices --template data
```

The relative ID is always prefixed with the app name: `example` becomes `myapp.example`. By default, the name comes from `[project].name` in the app root's `pyproject.toml`, falling back to the root directory name. Inferred names are lowercased and hyphens become underscores. Pass `--app-name myapp` to select another namespace, including when the runtime `AppConfig.app_id` differs from the project name. The generator reads static metadata and does not execute the app script.

`--app-root PATH` selects the application directory; its default is the current directory for authoring. Choose the directory used by the application entry script or its explicit `AppConfig.app_root`. For example:

```sh
mkapp plugin create prices -t data --app-root examples/market --app-name myapp --dry-run
```

Templates are `empty` (default, inherited no-op plugin hooks), `ui` (one floating window with a local counter), and `data` (one bounded synthetic sample action). Both `-t` and `--template` select the template. The command generates only `plugins/myapp.example/mkapp-plugin.toml` and `plugins/myapp.example/myapp_example/__init__.py`. Existing destinations, invalid names, package conflicts and symlinked plugin folders are refused. Dry runs write no files. Generation stages and validates files, reserves the destination exclusively and publishes the descriptor last; a failed publication cleans up its own files.

Restart the existing application after creation. Explicit allowlists, saved toggles, safe mode and source policy still control selection; the generator does not change them. Data consumers call `<prefixed-id>.sample.v1` with a map such as `{"count": 8}` and receive `{"values": [...]}`. Counts are limited to `1..128`. Cross-plugin grants remain application-owned. UI starters work with all optional extensions disabled. No dependencies are installed or downloaded.

## Annotated actions

The full provider is [synthetic_prices](examples/market/plugins/provider/synthetic_prices/__init__.py):

```python
from mkapp.sdk import ActionContext, Plugin, PluginContext, action, contract

@contract
class Input:
    count: int = 32

@contract
class Output:
    prices: list[float]

@action("example.prices.series.v1")
async def Series(request: Input, ctx: ActionContext) -> Output:
    return Output([100.0 + i / 10 for i in range(request.count)])

class Prices(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Series)

def create_plugin() -> Plugin:
    return Prices()
```

`@contract` is a frozen dataclass alias. `@action` generates an `Action` subclass, checked at registration. Input/output dataclass annotations compile once. Unsupported schemas, unknown fields, wrong primitive types and bad responses are rejected. Add bounded application validation in `__post_init__`, as the runnable provider does.

A handwritten `Action` instead declares `id`, `input`, `output` and async `handle(payload, ctx)`. Plain validator functions assigned as class attributes should use `staticmethod`. Keep action instances stateless; business state belongs to their owning service.

Runtime code calls `await ctx.actions.call("example.prices.series.v1", {"count": 8})`. A local Action class or `ActionRef` is normalized to the same ID and wire route. Cross-plugin consumers use contract IDs without importing provider implementations. The application grants cross-plugin action access explicitly.

## Windows and components

See the [independent viewer](examples/market/plugins/viewer/synthetic_viewer/__init__.py). Register a Window subclass with `ctx.windows.register(MyWindow, open_on_start=True)`. Each open constructs a separate no-argument instance on the GUI thread. `on_open`, `on_close` and `visible` have defaults. `visible(ctx)` runs before Begin each frame and supports transient windows such as the palette. Ordinary windows use `presentation="window"`; the built-in palette uses `"bottom_strip"`.

A render callback submits work without waiting:

```python
ctx.submit(
    "example.prices.series.v1", {"count": 32},
    on_success=self.updated,
    on_error=self.failed,
)
```

Payloads are copied through the bounded codec. Callbacks execute at GUI frame boundaries and are discarded after close. `WindowContext.arguments` is an immutable opening snapshot. The host owns outer Begin/End; plugins balance their nested widget scopes.

With tiles enabled and the open action granted, submit `mkapp.windows.open.v1` with:

```python
{
    "factory_id": "example.viewer.main.v1",
    "arguments": {},
    "placement": {
        "anchor_window": ctx.instance_id,
        "side": "below",
        "ratio": 0.4,
    },
}
```

The result contains `instance_id` and `fallback`. Stale/floating anchors use root fallback; native docking IDs never cross the bus. Cross-plugin opens require a declared provider dependency. The current implementation has no strict-anchor mode or layout persistence.

Register a reusable renderer with `ctx.components.register("example.widgets.badge.v1", renderer)`. Its synchronous GUI signature is `renderer(immutable_data, emit)`. A Window calls `ctx.components.render(id, instance_name, data, emit)`; the host scopes ImGui IDs by window, component and instance. Cross-plugin consumers declare the component owner's dependency.

## Workers and private operations

[Workbench jobs](examples/workbench/plugins/demo/example_workbench/jobs.py) are importable functions. Register a named job during prepare:

```python
ctx.workers.register(
    "example.workbench.cpu", "example_workbench.jobs:sum_squares",
    kind="process", max_concurrency=1,
)
```

An Action awaits `ctx.workers.submit(name, primitive_payload)`. Kinds are explicitly `async`, `thread` or `process`. Spawned processes receive MessagePack bytes and trusted job identifiers. Thread work must have bounded I/O/runtime; arbitrary threads cannot be forcibly cancelled. A caller timeout does not free admission until the underlying work finishes, and no timed-out side effect is retried automatically.

Private operations use a separate direct service, never the action bus:

```python
ctx.private.register(
    "example.workbench.length.v1",
    lambda value: {"length": len(value.reveal())},
    safe_result=True,
)
handle = ctx.private.hold(SecretValue("SYNTHETIC-PRIVATE-INPUT"))
safe = await ctx.private.invoke("example.workbench.length.v1", handle)
```

Import `SecretValue` from `mkapp.sdk`. Without `safe_result=True`, an operation returns another private handle. A handle grant names one grantee, operation and bounded TTL. Revocation, expiry and owner cleanup invalidate it. Native credentials use `await ctx.private.set_credential(name, SecretValue(...))` and `await ctx.private.credential(name)`; the latter returns a handle, not a raw password. These calls are runtime-thread APIs, not GUI widget APIs. Direct private GUI prompts and encrypted-document persistence remain outstanding.

## State, cache and local storage

Register only enabled features during prepare:

```python
ctx.state.register(StateSpec("example.workbench.runs", lambda v: v, persist=True))
ctx.cache.register(NamespaceSpec("example.workbench.cache", lambda v: v))
ctx.local_storage.register(NamespaceSpec("example.workbench.presets", lambda v: v))
```

Import the specs from `mkapp.sdk`. State persistence additionally requires `state_persistence`. Validate application shapes with the supplied validators; the global primitive/depth/item/frame limits apply independently. The workbench guards these registrations with `ctx.features.has(...)`.

Runtime operations use the bus through these facades:

```python
old = await ctx.state.get("example.workbench.runs")
await ctx.state.set("example.workbench.runs", 1, expected_revision=old["revision"])
await ctx.cache.put("example.workbench.cache", "sample", None)
await ctx.local_storage.put("example.workbench.presets", "demo", {"label": "Synthetic"})
```

State returns `found`, `value`, `revision`; stale CAS raises `conflict`. Cache returns `hit`, distinguishing cached null from a miss, and uses monotonic TTL and LRU budgets. Storage returns `found`, acknowledges writes after commit, and uses one DuckDB owner thread. Namespace access is owner-only. Other plugins call the owner's public actions.

## Commands, events and management

The workbench registers a `CommandSpec` for `workbench calculate count=1000`. `CommandField` supports string, integer, number, boolean and enum types, with typed defaults and optional nullability. Quoted strings use JSON escaping. Parsing rejects duplicate/unknown fields and executable expressions. Raw command history is never persisted. Autocomplete is a separate `autocomplete` extension requiring palette; completion providers register action IDs and return bounded `{text,start,end,label}` insertion records using character offsets.

Event declarations are metadata only:

```python
@on(BaseEvents.START)
async def app_started(self, payload):
    pass

# In prepare:
ctx.events.subscribe(self.app_started)
```

Use SDK imports `on` and `BaseEvents`. The host acknowledges prepared exact subscriptions before readiness. Events are bounded and best effort; mandatory setup/cleanup stays in lifecycle hooks and resource scopes.

Enable `plugin_manager` for the private management window or `registry_announcements` for opt-in public snapshots/events. Each is independent. Management saves desired toggles for restart without changing running code. Supply `run(config, relaunch=application_callback)` to make restart available; the callback runs only after shutdown. Package installation is unavailable. No plugin is treated as untrusted/sandboxed code.
