# mkapp project examples

Archived preimplementation design snapshot. Statements below about an unimplemented framework describe that earlier state. Use [current runnable examples](../EXAMPLES.md) and the [acceptance record](../IMPLEMENTATION.md) for the implemented API.

These are proposed SDK usage examples, not executable examples of an existing implementation. mkapp currently contains design documents only. Names introduced here, such as `AppConfig`, `WindowContext` and `CommandSpec`, illustrate the intended API and must be finalized in M1. Do not install an unrelated package named mkapp to run them.

Plugins inherit `Plugin` and windows inherit `Window`. Write an action as an `Action` subclass or use `@action(id)` on an async function to generate the subclass. For annotated actions, `@contract` is shorthand for `@dataclass(frozen=True)`. Register classes explicitly. The host constructs their instances and supplies `PluginContext`, `WindowContext` or invocation-scoped `ActionContext`. Optional lifecycle hooks have no-op defaults. All example data is synthetic and non-sensitive except for explicitly marked private handles. Choose only the examples needed by an application.

## 1. Small application and a development plugin

An application owns configuration and launch. A plugin owns its feature. A suggested project contains these files:

| Path | Purpose |
| --- | --- |
| `app.py` | Application entry point |
| `plugins/greeting/mkapp-plugin.toml` | Static development descriptor |
| `plugins/greeting/example_greeting/__init__.py` | Empty Python package marker |
| `plugins/greeting/example_greeting/plugin.py` | Feature implementation |

`app.py`:

```python
from mkapp import AppConfig, run


def main() -> None:
    run(AppConfig(
        app_id="example.desktop",
        title="Example desktop",
    ))


if __name__ == "__main__":
    main()
```

At startup, mkapp looks for `plugins/` next to `app.py` and discovers `plugins/greeting` from its descriptor. Eligible discovered plugins start by default. Set `enabled_plugins=("example.greeting",)` to run only that plugin, or `enabled_plugins=()` to select none. Keep only trusted code in the folder.

`extensions` selects optional framework features. Omitting it has the same effect as `extensions=()`. For example, set `extensions=("palette", "autocomplete")` to enable the command palette and its completion suggestions. Application plugins are discovered independently, and basic floating windows work with no extensions enabled.

The main guard supports spawned process workers. `run` starts the GUI on the main thread and manages the background runtime.

`mkapp-plugin.toml`:

```toml
manifest_version = 1
id = "example.greeting"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "example_greeting.plugin:create_plugin"
announce = false
required_features = []
```

`example_greeting/plugin.py`:

```python
from mkapp.sdk import Plugin, PluginContext, Window, WindowContext, imgui_bundle

imgui = imgui_bundle.imgui


class GreetingWindow(Window):
    id = "example.greeting.main.v1"
    title = "Greeting"

    def __init__(self):
        self.clicks = 0

    def render(self, ctx: WindowContext) -> None:
        imgui.text("Hello from a plugin")
        if imgui.button("Count"):
            self.clicks += 1
        imgui.text(f"Clicks: {self.clicks}")


class GreetingPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.windows.register(GreetingWindow, open_on_start=True)


def create_plugin() -> Plugin:
    return GreetingPlugin()
```

Window convention: the host constructs a separate no-argument instance on the GUI thread, then calls `on_open(ctx)`, synchronous `render(ctx)` and finally `on_close(ctx)`. Validated opening arguments are available through `ctx.arguments`. Open/close hooks are optional; render is required. The host owns outer Begin/End and instance-ID scoping. Each instance has its own click count. `open_on_start` schedules opening only after the plugin becomes ready. No `start`, `stop` or superclass calls are needed.

`imgui_bundle` is the upstream module exposed lazily through the SDK. The window calls the original widget API on the GUI thread.

## 2. Two headless plugins communicating through actions

A catalog plugin owns synthetic catalog data. A separate summary plugin depends on it. Neither has a window or requires palette, tiles, state or public registry announcements.

The examples use a shared **contract-only** package, `example_contracts.catalog`, containing an action name and pure validators. This package is an ordinary Python dependency; it contains no service implementation and starts no runtime work.

```python
# example_contracts/catalog.py
LIST_ITEMS = "example.catalog.list.v1"


def empty_payload(value):
    if not isinstance(value, dict) or value:
        raise ValueError("Expected an empty map")
    return value


def catalog_result(value):
    if not isinstance(value, dict) or set(value) != {"items"}:
        raise ValueError("Invalid result shape")
    items = value["items"]
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("Invalid item list")
    if not all(isinstance(item, str) and len(item) <= 80 for item in items):
        raise ValueError("Invalid item")
    return value
```

Validator convention: a validator returns the accepted wire value or raises a validation error. Wrap plain validator functions assigned as class metadata with `staticmethod` so Python does not bind them as instance methods. The host sanitizes errors and applies global size/depth limits independently.

```python
# example_catalog/plugin.py
from mkapp.sdk import Action, ActionContext, Plugin, PluginContext
from example_contracts.catalog import LIST_ITEMS, empty_payload, catalog_result


class ListItemsAction(Action):
    id = LIST_ITEMS
    input = staticmethod(empty_payload)
    output = staticmethod(catalog_result)

    async def handle(self, payload, ctx: ActionContext):
        return {"items": ["Sample A", "Sample B"]}


class CatalogPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(ListItemsAction)


def create_plugin() -> Plugin:
    return CatalogPlugin()
```

The consumer descriptor declares the runtime dependency:

```toml
manifest_version = 1
id = "example.summary"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "example_summary.plugin:create_plugin"
announce = false
required_features = []

[requires]
"example.catalog" = ">=0.1,<0.2"
```

```python
# example_summary/plugin.py
from mkapp.sdk import Plugin, PluginContext
from example_contracts.catalog import LIST_ITEMS


class SummaryPlugin(Plugin):
    async def start(self, ctx: PluginContext) -> None:
        result = await ctx.actions.call(LIST_ITEMS, {}, timeout_ms=1000)
        self.item_count = len(result["items"])


def create_plugin() -> Plugin:
    return SummaryPlugin()
```

Place both plugins in `plugins/` to discover them at startup. If using an explicit `enabled_plugins` allowlist, include both IDs. The host starts catalog first. The call passes through ZeroMQ and MessagePack even though both plugins run in one process. `call` is runtime-thread-only. GUI code uses `ui.submit`. Application policy must grant summary access to the catalog action. A missing or disabled catalog blocks summary before import.

## 3. CPU work without blocking the GUI

A plugin declares a named process worker and exposes an action that uses it. A window submits the action and receives completion at a frame boundary.

```python
# example_compute/jobs.py — importable by a spawned child
def sum_squares(payload):
    limit = payload["limit"]
    return {"total": sum(n * n for n in range(limit))}
```

```python
# example_compute/plugin.py
from mkapp.sdk import (
    Action, ActionContext, Plugin, PluginContext, Window, WindowContext, imgui_bundle,
)

imgui = imgui_bundle.imgui

CALCULATE = "example.compute.calculate.v1"


def calculation_input(value):
    if not isinstance(value, dict) or set(value) != {"limit"}:
        raise ValueError("Expected limit")
    limit = value["limit"]
    if type(limit) is not int or not 0 <= limit <= 1_000_000:
        raise ValueError("Invalid limit")
    return value


def calculation_result(value):
    if not isinstance(value, dict) or set(value) != {"total"}:
        raise ValueError("Invalid result")
    if type(value["total"]) is not int or not 0 <= value["total"] < 2**63:
        raise ValueError("Invalid total")
    return value


class CalculateAction(Action):
    id = CALCULATE
    input = staticmethod(calculation_input)
    output = staticmethod(calculation_result)
    max_concurrency = 1

    async def handle(self, payload, ctx: ActionContext):
        return await ctx.workers.submit("example.compute.sum_squares", payload)


class CalculationWindow(Window):
    id = "example.compute.main.v1"
    title = "Compute"

    def __init__(self):
        self.pending = False
        self.status = "Ready"

    def render(self, ctx: WindowContext) -> None:
        imgui.text(self.status)
        if imgui.button("Calculate") and not self.pending:
            self.pending = True
            self.status = "Working"
            ctx.submit(
                CalculateAction, {"limit": 1_000_000},
                on_success=self.completed,
                on_error=self.failed,
            )

    def completed(self, result):
        self.pending = False
        self.status = f"Total: {result['total']}"

    def failed(self, error):
        self.pending = False
        self.status = f"Failed: {error.code}"


class ComputePlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.workers.register(
            "example.compute.sum_squares",
            "example_compute.jobs:sum_squares",
            kind="process", max_concurrency=1,
        )
        ctx.actions.register(CalculateAction)
        ctx.windows.register(CalculationWindow, open_on_start=True)


def create_plugin() -> Plugin:
    return ComputePlugin()
```

`submit` must return immediately, including when admission fails; failures are delivered through the GUI completion bridge. Success/error callbacks execute on the GUI thread before rendering, not on the worker or runtime thread. Window-scoped submissions are cancelled/discarded after window closure. The Action class is normalized to its ID before serialization; its singleton instance retains no request state. The SDK bounds deadlines/queues and tracks the worker in the plugin scope; no plugin-created executor or custom cleanup is necessary. No sensitive input is accepted by ordinary workers.

## 4. Open another window below the current tile

The action opens a previously registered Window subclass; it does not pass native docking IDs. This is a method on a GUI-owned window object like those above:

```python
def render(self, ctx: WindowContext) -> None:
    if imgui.button("Open details below"):
        ctx.submit(
            "mkapp.windows.open.v1",
            {
                "factory_id": "example.details.main.v1",
                "arguments": {},
                "placement": {
                    "anchor_window": ctx.instance_id,
                    "side": "below",
                    "ratio": 0.35,
                },
            },
            on_success=self.opened,
            on_error=self.failed,
        )

def opened(self, result):
    self.status = "Opened"

def failed(self, error):
    self.status = f"Open failed: {error.code}"
```

Declare `required_features = ["tiles"]` in the caller's descriptor. If details belongs to another plugin, declare that plugin in `[requires]` and enable both. The host rechecks anchor lifetime on the GUI thread, applies the split before rendering and reports the configured fallback if the anchor disappeared. A more flexible plugin can guard `ctx.features.has("tiles")` and offer a floating-window action without placement when tiles are off.

## 5. Optional palette command with typed fields

An existing plugin contributes a command only if palette is enabled. Its action remains available without palette. This snippet belongs in `prepare` after the action is registered:

```python
from mkapp.sdk import CommandSpec, FieldSpec

if ctx.features.has("palette"):
    ctx.commands.register(CommandSpec(
        id="example.compute.command.v1",
        path=("compute", "sum"),
        description="Calculate a synthetic sum of squares",
        fields=(FieldSpec(
            name="limit", kind="integer", default=1000,
            minimum=0, maximum=1_000_000,
        ),),
        action="example.compute.calculate.v1",
    ))
```

The user enters `compute sum limit=100000`. The parser validates the field, then the bus validates the action contract again. The action handler is the same one used by the Compute window. Command field values map to the action payload by field name.

With the separate autocomplete extension enabled, the catalog supplies `compute`, then `sum`, then `limit=` suggestions. No completion provider is needed for these static suggestions. With autocomplete disabled, the command still works. Sensitive values must use a private prompt rather than an inline field.

## 6. Reuse a component without importing another plugin

A component provider registers a small render factory. The host instantiates it inside a caller-scoped ImGui ID boundary:

```python
# In the component-provider module
from mkapp.sdk import Plugin, PluginContext, imgui_bundle

imgui = imgui_bundle.imgui


class PublicCount:
    def render(self, view, *, on_refresh):
        imgui.text(f"Items: {view['count']}")
        if imgui.button("Refresh"):
            on_refresh()


class WidgetsPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.components.register(
            "example.widgets.count.v1", factory=PublicCount,
        )


def create_plugin() -> Plugin:
    return WidgetsPlugin()
```

The consuming plugin declares a dependency on `example.widgets`. Its window uses the GUI facade to create a scoped component instance:

```python
from mkapp.sdk import Window, WindowContext, imgui_bundle

imgui = imgui_bundle.imgui


class OverviewWindow(Window):
    id = "example.overview.main.v1"
    title = "Overview"

    def __init__(self):
        self.count = 0
        self.status = "Ready"

    def on_open(self, ctx: WindowContext):
        self.ui = ctx
        self.badge = ctx.components.create(
            "example.widgets.count.v1", instance_id="overview-count",
        )

    def render(self, ctx: WindowContext):
        self.badge.render({"count": self.count}, on_refresh=self.refresh)
        imgui.text(self.status)

    def refresh(self):
        self.ui.submit(
            "example.catalog.list.v1", {},
            on_success=self.updated, on_error=self.failed,
        )

    def updated(self, result):
        self.count = len(result["items"])

    def failed(self, error):
        self.status = f"Refresh failed: {error.code}"
```

The consumer also depends on catalog and is granted its action. Component calls are local GUI composition; fetching catalog data still uses the bus. The component owns no business service or hidden worker, and the consumer never imports the provider's implementation module. Component instances belong to the window scope and are disposed with it.

## 7. Sensitive workflow: unlock, process, show a safe result

Private API signatures require security review before implementation. This example specifies the flow without inventing a supposedly safe implementation of credential entry, encryption or declassification:

| Step | Execution path | Data allowed |
| --- | --- | --- |
| User selects “Unlock sample vault” | GUI submits a normal action | Empty payload only |
| Owner opens a private prompt | Host private UI bridge | Window-local private input |
| User supplies a credential | Direct `ctx.private` service | Sensitive wrapper, never a bus field |
| Vault owner unlocks synthetic records | Bounded private executor | Scoped handles and protected internal values |
| Approved consumer requests a calculation | Direct private operation call | Owner/grantee/operation-bound handle |
| UI receives completion | Dedicated private completion bridge | Private handle or explicitly approved safe status |

An ordinary bus reply can acknowledge that the unlock prompt was scheduled. It must not return a private handle, credential, decrypted record, document name or raw exception. A safe status such as `unlocked=true` still needs application approval because it reveals information.

The vault plugin declares and registers private operations, while application policy grants individual consumers access. The consumer declares its plugin dependency as usual; that dependency alone does not grant private access. Cancellation, lock, expiry and plugin cleanup revoke handles. If an approved backend is unavailable, unlock/persistence fails closed. This exception to bus communication does not create isolation from malicious in-process plugins.

## 8. Ship the same plugin as a wheel

The module and factory do not change. Packaging adds an entry point and includes the same static descriptor as a resource under its import package:

| Development path | Installed wheel path |
| --- | --- |
| `plugins/greeting/mkapp-plugin.toml` | `example_greeting/mkapp-plugin.toml` |
| `plugins/greeting/example_greeting/plugin.py` | `example_greeting/plugin.py` |

Relevant `pyproject.toml` metadata fragment:

```toml
[project]
name = "example-greeting-plugin"
version = "0.1.0"
dependencies = ["mkapp>=0.1,<0.2"]

[project.entry-points."mkapp.plugins"]
"example.greeting" = "example_greeting.plugin:create_plugin"
```

This is a metadata fragment, not a complete build configuration. Use the selected build backend's package-data configuration to include the descriptor; verify it in the built wheel. Maintain one canonical descriptor and copy it during packaging rather than hand-maintaining divergent copies. Exact wheel build commands belong in the implemented packaging example once dependencies and tooling exist.

Install the wheel with standard tooling into the app environment and approve its distribution. The plugin is eligible by default. If an explicit allowlist is in use, include `example.greeting`. Use either the development copy or the installed wheel for this plugin ID. The entry point discovers the plugin, and the descriptor controls SDK compatibility and runtime plugin requirements. Restart to load the newly installed plugin.

## 9. Cache a derived catalog and save a workspace preset

This headless plugin saves explicitly non-sensitive presets across restarts. It also caches an index of a synthetic catalog. Enable `local_storage` and configure a per-app DuckDB path. Install the optional `mkapp[duckdb]` extra to use the built-in provider. The cache can be enabled independently.

```toml
# Fields from the plugin descriptor
required_features = ["local_storage"]
```

```python
from mkapp.sdk import ActionContext, CacheSpec, Plugin, PluginContext, StorageSpec


def short_text(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 80:
        raise ValueError("Expected bounded text")
    return value


def catalog_index(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("Expected a bounded catalog index")
    return [short_text(item) for item in value]


class WorkspacePlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.local_storage.register(StorageSpec(
            id="example.workspace.presets",
            schema_version=1,
            key=short_text,
            value=short_text,
        ))
        if ctx.features.has("cache"):
            ctx.cache.register(CacheSpec(
                id="example.workspace.catalog_index",
                key=short_text,
                value=catalog_index,
                ttl_seconds=60,
            ))


async def index_for_revision(
    ctx: ActionContext, revision: int, items: list[str]
) -> list[str]:
    cache_id = "example.workspace.catalog_index"
    key = str(revision)
    use_cache = ctx.features.has("cache")
    if use_cache:
        cached = await ctx.cache.get(cache_id, key)
        if cached.hit:
            return cached.value
    index = sorted(set(items))
    if use_cache:
        await ctx.cache.put(cache_id, key, index)
    return index


async def save_default_preset(ctx: ActionContext) -> None:
    await ctx.local_storage.put(
        "example.workspace.presets", "default", "Two columns"
    )
```

These helpers are called from registered actions owned by the plugin. The catalog action receives a bounded snapshot with its authoritative revision, and a changed revision leads to a fresh computation. If the app wants caching to remain best-effort under load, it can handle `busy` from cache calls by computing or returning the index without caching it. The saved preset can be read through the same namespace after restart. A successful put means the storage provider committed it. If a write times out, reconcile with a read after it has settled. Shared state and its persistence switch are independent of both features.

## 10. Observe application lifecycle events

A headless plugin can observe host lifecycle transitions through the same event API as domain events. Each `@on` declaration associates a method with one event. The explicit subscription gives the host an owner and a cleanup scope.

```python
from mkapp.sdk import BaseEvents, Plugin, PluginContext, on


class LifecycleObserver(Plugin):
    def __init__(self) -> None:
        self.observed_events: set[str] = set()

    def prepare(self, ctx: PluginContext) -> None:
        ctx.events.subscribe(self.app_started)
        ctx.events.subscribe(self.app_stopping)
        ctx.events.subscribe(self.app_escalating)

    @on(BaseEvents.START)
    async def app_started(self, payload: dict[str, object]) -> None:
        self.observed_events.add(BaseEvents.START)

    @on(BaseEvents.SHUTDOWN)
    async def app_stopping(self, payload: dict[str, object]) -> None:
        self.observed_events.add(BaseEvents.SHUTDOWN)

    @on(BaseEvents.KILL)
    async def app_escalating(self, payload: dict[str, object]) -> None:
        self.observed_events.add(BaseEvents.KILL)


def create_plugin() -> Plugin:
    return LifecycleObserver()
```

For a domain event, pass its exact versioned ID, such as `@on("example.catalog.changed.v1")`, and subscribe the same way. Base events can be dropped and are delivered only to ready owners. Put required cleanup in `stop` or a host-managed resource scope. Forced shutdown can occur without time to run the observer.

## 11. Build a market research workspace one feature at a time

Start with a synthetic price series. The first plugin can be one Python module and its descriptor. Add each consumer when the workflow needs it.

| Plugin | Contribution | Runtime dependencies |
| --- | --- | --- |
| `example.market_data` | A headless provider that owns the series and registers `example.market_data.series.v1` | None |
| `example.market_chart` | A floating window that requests a sample and plots it | `example.market_data` |
| `example.market_summary` | An independent analysis plugin that summarizes the same samples | `example.market_data` |

The series action takes a synthetic series ID and a bounded sample limit. It returns price ticks and a revision. The decorator takes only the ID. The SDK resolves the input and output annotations when the plugin registers the action.

```python
from mkapp.sdk import ActionContext, Plugin, PluginContext, action, contract


@contract
class SeriesInput:
    series: str
    limit: int

    def __post_init__(self) -> None:
        if self.series != "SYNTH-1" or not 1 <= self.limit <= 100:
            raise ValueError("Invalid series or sample limit")


@contract
class SeriesOutput:
    revision: int
    price_ticks: list[int]


@action("example.market_data.series.v1")
async def get_series(request: SeriesInput, ctx: ActionContext) -> SeriesOutput:
    return SeriesOutput(revision=1, price_ticks=[101, 103, 102][:request.limit])


class MarketDataPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(get_series)


def create_plugin() -> Plugin:
    return MarketDataPlugin()
```

Consumers still send a wire map. This request asks for at most three points.

```json
{"series": "SYNTH-1", "limit": 3}
```

The handler receives a `SeriesInput` object and returns a `SeriesOutput` object. Consumers receive the validated response as a wire map.

```json
{"revision": 1, "price_ticks": [101, 103, 102]}
```

1. Load the provider with optional extensions off. Give each consumer access to the series action through app policy when enabling it.
2. Put the chart plugin in the app's `plugins/` folder and restart. The chart submits the series action through its window context and renders the result when the GUI bridge delivers it. The provider stays unchanged.
3. Add the summary plugin using the same public contract. Disable the chart and restart. The summary still works because it depends only on the provider.

Start with a small in-memory fixture and keep sampling in the provider as the dataset grows. The provider can move expensive sampling into an SDK worker behind the same action contract. The chart and summary continue to receive bounded samples. See the [headless actions](#2-two-headless-plugins-communicating-through-actions) and [worker example](#3-cpu-work-without-blocking-the-gui) for the SDK patterns.

## Implementation checks for these examples

When M1 finalizes SDK names, turn these sketches into real example packages progressively. Validate actual directory/wheel loading, action grants, GUI callback ownership, field mapping, worker spawn and cleanup. Do not add a fake `mkapp` implementation just to make the examples import. Until the framework exists, syntax checks establish only that the Python snippets parse, not that they execute.
