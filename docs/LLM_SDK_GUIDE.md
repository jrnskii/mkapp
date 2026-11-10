# mkapp SDK guide for LLM coding agents

This is the authoring reference for the **current SDK 0.1 implementation**, checked against this repository on 2026-11-10. It is intended to be supplied to an LLM that creates or modifies an application plugin. Read [AGENTS.md](../AGENTS.md) before changing this repository. Examples use synthetic data. Requirements and delivery targets in [ARCHITECTURE.md](../ARCHITECTURE.md) and [PLAN.md](../PLAN.md) do not imply that every designed feature exists; consult [IMPLEMENTATION.md](../IMPLEMENTATION.md) for acceptance gaps.

## Contents

1. [Instructions for the coding agent](#instructions-for-the-coding-agent)
2. [Execution domains and object ownership](#execution-domains-and-object-ownership)
3. [Create and discover a plugin](#create-and-discover-a-plugin)
4. [Complete provider and viewer application](#complete-provider-and-viewer-application)
5. [Public types and service reference](#public-types-and-service-reference)
6. [Action contracts and serialization](#action-contracts-and-serialization)
7. [Windows and reusable components](#windows-and-reusable-components)
8. [Managed workers](#managed-workers)
9. [Events](#events)
10. [State, cache and local storage](#state-cache-and-local-storage)
11. [Commands and completion](#commands-and-completion)
12. [Private operations and credentials](#private-operations-and-credentials)
13. [Configuration, extensions and packaging](#configuration-extensions-and-packaging)
14. [Errors and troubleshooting](#errors-and-troubleshooting)
15. [Verification and delivery checklist](#verification-and-delivery-checklist)
16. [Unavailable APIs and known limits](#unavailable-apis-and-known-limits)

## Instructions for the coding agent

Use these instructions when implementing a request with mkapp:

1. Read the application's configuration and existing plugin descriptors. Identify the app root, namespace, enabled services, required providers and explicit action grants. Preserve unrelated plugins and user work.
2. Keep application domain logic in plugins. Import framework APIs from `mkapp.sdk`; only the application entry script imports `AppConfig`, `Limits` and `run` from `mkapp`. Never import another plugin's implementation or `mkapp.runtime`/`mkapp.ui` in plugin code.
3. Choose the smallest implementation: ordinary Python for logic within one plugin and execution domain; explicit SDK registration when exposing an action, window, worker, event, component or other host-owned resource.
4. Use one direct `Plugin` subclass and a no-argument factory returning a `Plugin`. Use `Window` and `Action` subclasses for their respective registrations. Decorated actions also become `Action` subclasses. Do not invent callable-handler registration or subclass discovery.
5. Make IDs stable and namespaced. Plugin IDs have at least two lowercase dotted segments; versioned action/window/event/component/command IDs end in `.v1`, `.v2`, etc. Use the owning plugin's ID as the prefix. Reserve `mkapp.*` for the host.
6. Keep render methods synchronous, short and GUI-only. Use `WindowContext.submit` for actions and GUI completion callbacks for results. All ImGui and graphics operations stay on the main thread.
7. Define bounded primitive wire contracts. Keep complete datasets, DataFrames, models and services with their owning plugin; send bounded summaries/pages/samples. Never pass arbitrary Python objects over the bus.
8. Guard optional contributions with `ctx.features.has(...)`. Declare a mandatory optional service in descriptor `required_features` so resolution can block before import. Do not enable services or widen allowlists/grants automatically.
9. Keep raw secrets and private handles out of action/worker payloads, state, cache, local storage, config, history and logs. Use direct private operations. Imported plugins are trusted Python code; there is no security sandbox.
10. Check the implemented signatures before generating advanced integrations. If a required capability is unavailable, state the gap and propose the smallest explicit contract change; do not fabricate an SDK API or hide a stub behind a successful response.

No SDK import should start GUI initialization, threads, processes, sockets or credential access. Avoid filesystem/network operations at module import. Access the lazy `imgui_bundle` export inside GUI methods to preserve headless import behavior.

## Execution domains and object ownership

| Code | Execution domain | Ownership and restrictions |
| --- | --- | --- |
| Plugin factory and `prepare/start/stop` | Runtime asyncio thread | Host creates the plugin and supplies `PluginContext`; no GUI calls or blocking I/O |
| `Action` construction and `handle` | Runtime thread | One instance per registration; concurrent calls can overlap; invocation data stays local |
| Event callback | Runtime thread | Async, bounded handler; delivery is best effort |
| `Window` construction, hooks, `visible`, `render` | Main GUI thread | A distinct instance for every open; constructor takes no arguments; no I/O or registration |
| GUI success/error callback | Main GUI thread at a frame boundary | Apply snapshots only while the window and owner are active |
| Component renderer and its intent callback | Main GUI thread | Render under caller/window/instance ID scopes; no blocking work |
| `async` worker | Runtime event loop | Async function; genuinely non-blocking operations only |
| `thread` worker | Managed thread pool | Synchronous importable function; bounded I/O; no ImGui, context or secrets |
| `process` worker | Spawned process pool | Synchronous importable function; CPU work; bounded primitive inputs/results |
| Private operation callback | Separate private executor | Synchronous callback accepting `SecretValue`; never ordinary workers |

`async def` does not make a CPU calculation or synchronous network call non-blocking. Do not use `time.sleep`, blocking clients, `.result()`, `asyncio.run`, executor shutdown waits or disk/database calls in a render method.

Plugin lifecycle defaults are no-ops. Override only the needed hooks; no `super()` call is required. Register contributions in synchronous `prepare`; use async `start` for required runtime initialization and async `stop` for plugin-owned cleanup. Required dependencies start first and stop after their dependents. Readiness follows successful start. Host cleanup disposes registered resources even after partial failure or a failing `stop`.

Registrations return idempotent `Registration.close()` handles already tracked by the host. Keep a handle only if you need early disposal, and close it on the owning runtime thread. `PluginContext` has no public `scope`, `tasks`, `spawn` or `create_task` field. Do not assume arbitrary `asyncio.create_task` calls are host-tracked; custom long-lived tasks require explicit bounded ownership and cleanup by the plugin.

## Create and discover a plugin

Requires CPython `>=3.14,<3.15`. In this repository use `uv run mkapp`; in an environment where mkapp is installed use `mkapp`.

```sh
mkapp plugin create example
mkapp plugin create dashboard -t ui
mkapp plugin create prices --template data
mkapp plugin create prices -t data --app-root ./myapp --app-name myapp --dry-run
```

`empty` is the default; `ui` creates a floating counter window; `data` creates a bounded synthetic sample action. Both `-t` and `--template` select a starter. The supplied ID is relative and **always** prefixed: `example` in app `myapp` becomes `myapp.example`. Do not pass `myapp.example` expecting the generator to remove a repeated prefix.

Authoring root defaults to the current directory. App name comes from `[project].name` in that root's `pyproject.toml`, falling back to the directory name; inferred names are lowercased and hyphens become underscores. `--app-name` overrides inference. The generator does not execute the application to read `AppConfig.app_id`. A project named `mkapp` needs an application namespace override because `mkapp.*` is reserved.

For `myapp.example` the generated layout is:

```text
myapp/
  app.py
  plugins/
    myapp.example/
      mkapp-plugin.toml
      myapp_example/
        __init__.py
```

Only the descriptor and Python module are generated; `app.py` belongs to the application. Empty/data starters are headless. No installation, config mutation, grants, downloads or hot loading occur. Generation refuses existing destinations and detected package/ID conflicts. Dry run writes nothing. Activation requires application restart and selection by the application's normal source/enablement policy.

Runtime discovery uses `plugins/` next to the entry script or explicit `AppConfig.app_root`, **not necessarily the working directory**. It scans immediate child directories only. Each needs a descriptor and uniquely named Python package. Additional individual plugin roots and installed distributions require explicit application approval. Metadata is checked before import; duplicate IDs/packages and escaping paths are rejected.

Current descriptor fields:

```toml
manifest_version = 1
id = "myapp.viewer"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "myapp_viewer:create_plugin"
announce = false
required_features = []

[requires]
"myapp.prices" = ">=0.1,<0.2"

[optional]
"myapp.labels" = ">=0.1,<0.2"
```

Omit dependency tables when unnecessary. Optional dependencies do not cause installation or auto-enablement. `announce` controls public registry visibility, not private inventory or discovery. Do not add speculative descriptor fields: the current parser rejects unknown fields. Python distribution dependencies are separate from runtime plugin dependencies.

## Complete provider and viewer application

The following five files form a minimal application. They use floating windows, synthetic data, no optional extensions, no plugin-internal imports and an explicit cross-plugin grant.

### `app.py`

```python
from pathlib import Path

from mkapp import AppConfig, run


if __name__ == "__main__":
    run(AppConfig(
        app_id="myapp.desktop",
        app_root=Path(__file__).parent,
        grants={"myapp.viewer": ("myapp.prices.sample.v1",)},
        extensions=(),
    ))
```

### `plugins/myapp.prices/mkapp-plugin.toml`

```toml
manifest_version = 1
id = "myapp.prices"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "myapp_prices:create_plugin"
announce = false
```

### `plugins/myapp.prices/myapp_prices/__init__.py`

```python
from mkapp.sdk import ActionContext, Plugin, PluginContext, action, contract


@contract
class SampleInput:
    count: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.count <= 128:
            raise ValueError


@contract
class SampleOutput:
    values: list[float]


@action("myapp.prices.sample.v1")
async def Sample(request: SampleInput, ctx: ActionContext) -> SampleOutput:
    return SampleOutput([100.0 + i / 10 for i in range(request.count)])


class Prices(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.actions.register(Sample)


def create_plugin() -> Plugin:
    return Prices()
```

### `plugins/myapp.viewer/mkapp-plugin.toml`

```toml
manifest_version = 1
id = "myapp.viewer"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "myapp_viewer:create_plugin"
announce = false

[requires]
"myapp.prices" = ">=0.1,<0.2"
```

### `plugins/myapp.viewer/myapp_viewer/__init__.py`

```python
from mkapp.sdk import AppError, Plugin, PluginContext, Window, WindowContext


class PricesWindow(Window):
    id = "myapp.viewer.main.v1"
    title = "Synthetic sample"

    def __init__(self) -> None:
        self.values: list[float] = []
        self.status = "Ready"
        self.pending = False

    def refresh(self, ctx: WindowContext) -> None:
        if self.pending:
            return
        self.pending = True
        self.status = "Loading"
        try:
            ctx.submit(
                "myapp.prices.sample.v1",
                {"count": 8},
                on_success=self.updated,
                on_error=self.failed,
            )
        except AppError as error:
            self.failed(error)

    def updated(self, result: dict) -> None:
        self.values = result["values"]
        self.status = "Loaded"
        self.pending = False

    def failed(self, error: AppError) -> None:
        self.status = error.code
        self.pending = False

    def render(self, ctx: WindowContext) -> None:
        from mkapp.sdk import imgui_bundle

        imgui = imgui_bundle.imgui
        imgui.text(self.status)
        if imgui.button("Refresh"):
            self.refresh(ctx)
        for index, value in enumerate(self.values):
            imgui.text(f"{index}: {value:.2f}")


class Viewer(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.windows.register(PricesWindow, open_on_start=True)


def create_plugin() -> Plugin:
    return Viewer()
```

Run `python app.py` in the installed environment, or `uv run python app.py` in a uv project. Click Refresh to request a sample. A provider dependency establishes lifecycle order; the app grant separately authorizes the route. Both are needed for this integration. Adding/disabling the viewer leaves the provider implementation unchanged.

## Public types and service reference

Import these from `mkapp.sdk`. Contexts are host-created; never instantiate them to manufacture authority.

### Base classes and values

| Type | Current contract |
| --- | --- |
| `Plugin` | `prepare(ctx: PluginContext) -> None`; async `start(ctx)` / `stop(ctx)`; all default to no-op |
| `Action` | `id`, `input`, `output`; async `handle(payload, ctx: ActionContext)`; defaults: `timeout_ms=5000`, `max_concurrency=1`, `idempotent=False` |
| `Window` | `id`, `title`, synchronous `render(ctx: WindowContext)`; optional no-op `on_open` / `on_close`; `visible(ctx)` defaults true; `presentation="window"` |
| `ActionRef` | Frozen record with `id: str`; safe reference to a route, not a provider instance |
| `AppError` | Fixed safe `code: str`; do not expose arbitrary exception strings |
| `Registration` | Runtime-owned, idempotent `close()` disposal handle |
| `Features` | `has(name: str) -> bool` |
| `ResourceScope` | Exported host resource primitive; not a service available through `ctx.scope` |
| `SecretValue` / `PrivateHandle` | Redacted sensitive wrapper / opaque host-managed handle; neither is wire data |

### Context fields

| Context | Fields |
| --- | --- |
| `PluginContext` | `owner`, `actions`, `windows`, `events`, `workers`, `private`, `state`, `cache`, `local_storage`, `components`, `commands`, `completions`, `features` |
| `ActionContext` | `source`, `owner`, `correlation_id`, `deadline`, `actions`, `workers` |
| `WindowContext` | `instance_id`, `arguments`, `submit`, `components` |

`ActionContext.deadline` uses monotonic time. Caller/source identity is stamped by the host. Never retain an invocation context on an action or service. `ActionContext` has no `.private`, `.state`, `.events` or `.features`. Window code has no `.actions`, `.workers` or `.private`. Do not move `PluginContext` into windows or workers to bypass these boundaries.

For an ordinary action that needs state/cache/storage, call the registered service actions using `ctx.actions`; see the payload table below. For persistent plugin business state, use an owner service rather than request fields on the Action instance. There is no generic service container or action dependency-injection API: do not invent constructor arguments or a hidden context injection convention.

### Registration and calls

The following are signatures, not additional functions to define. All registration happens on the runtime thread during `prepare` unless stated otherwise.

```text
ctx.actions.register(ActionClass) -> Registration
await ctx.actions.call(target, payload, *, timeout_ms=5000) -> wire_value
ctx.windows.register(WindowClass, *, open_on_start=False) -> Registration
ctx.events.register(identifier, validator) -> Registration
ctx.events.subscribe(async_callback_with_on_metadata) -> Registration
ctx.events.subscribe(identifier, async_callback) -> Registration
await ctx.events.publish(identifier, payload) -> None
ctx.workers.register(name, "package.module:function", *, kind, max_concurrency=1)
await ctx.workers.submit(name, payload, *, timeout_ms=5000) -> wire_value
ctx.components.register(identifier, renderer) -> Registration
ctx.commands.register(CommandSpec(...)) -> Registration
ctx.completions.register(action_id) -> Registration
ctx.state.register(StateSpec(...)) -> Registration
ctx.cache.register(NamespaceSpec(...)) -> Registration
ctx.local_storage.register(NamespaceSpec(...)) -> Registration
```

Action targets may be a string ID, `ActionRef`, or local `Action` class. Each follows the same validated wire path; class references do not permit direct execution. Cross-plugin consumers use public IDs or a separately shared contract package, never import the provider implementation.

## Action contracts and serialization

`@contract` is equivalent to `@dataclass(frozen=True)`, not a registry, validator service or transport. Frozen records are shallowly immutable; nested lists can still be mutable. `@action("id")` turns an async function into an `Action` subclass; explicitly register that class in `prepare`.

Annotated actions require exactly two positional parameters with no parameter defaults: a dataclass input and `ActionContext`, plus a dataclass return annotation. Field defaults belong in the input record. Annotations are resolved and adapters compiled at registration. Use module-level records and resolvable annotations, not function-local forward references.

Supported field schemas in the current adapter:

| Supported | Constraints |
| --- | --- |
| `str`, `bytes`, `int`, `float`, `bool`, `None` | Exact primitive type; a boolean is not an integer; an integer is not a float |
| `T | None` / `Optional[T]` | A nullable supported type; not arbitrary unions |
| `list[T]` | Actual list of supported values |
| `dict[str, T]` | Actual dict, string keys, supported values |
| Frozen dataclass records | Nested supported fields, finite non-recursive schemas, constructor fields and resolvable annotations |

Do not assume support for `Any`, tuple/set, `Mapping`, Enum, Literal, arbitrary unions, recursive dataclasses, datetime, Decimal, Path, NumPy scalar/array, DataFrame, Pydantic models or custom objects. Convert such values explicitly into a documented bounded wire shape. Put business size/range checks in `__post_init__` or handwritten validators; annotations alone do not bound a list length or numeric range.

The host decodes the map and checks fields before constructing a dataclass, rejects extra fields, applies validated defaults, and validates the response before encoding. The handler receives `SampleInput`; callers receive a map such as `{"values": [100.0]}`, **not** `SampleOutput`. Supply primitive maps at call sites; do not send dataclass instances as bus payloads.

A handwritten alternative using the same contracts:

```python
from mkapp.sdk import Action, ActionContext


class SampleAction(Action):
    id = "myapp.prices.sample.v1"
    input = SampleInput
    output = SampleOutput
    timeout_ms = 5000
    max_concurrency = 1
    idempotent = True

    async def handle(self, payload: SampleInput, ctx: ActionContext) -> SampleOutput:
        return SampleOutput([100.0 + i / 10 for i in range(payload.count)])
```

This fragment reuses the records above. Register it **instead of** the decorated `Sample`, because duplicate IDs fail. Plain callable validators assigned as class attributes should use `staticmethod(validate)` to avoid accidental method binding. Validators return the accepted/normalized value and raise on invalid data; returning `False` is a value, not rejection.

Action instances have per-registration lifetime. Keep them stateless by convention even with concurrency one; never retain payloads, callers or invocation contexts on `self`. Construction takes no arguments and must do no I/O, registration or executor creation. `idempotent=True` documents intent; it does not authorize automatic retries.

The ordinary wire subset is `None`, exact booleans, bounded integers, finite floats, UTF-8 strings, bytes, lists and maps with string keys. NaN/infinity, extensions, duplicate map keys and arbitrary object serialization are rejected. Default host budgets include 1 MiB frames, depth 16, 10,000 visited items, 256 pending calls, concurrency 64 and a 5-second action ceiling. These are host limits, not a target payload size; metadata/envelopes consume space too. Prefer much smaller results.

No pickle, `send_pyobj`, native sockets, Python contexts, private handles or implementation objects belong in messages. Do not retain raw payload captures in diagnostics.

## Windows and reusable components

The host constructs a separate `Window` instance for each open on the GUI thread. Set mutable view state on that instance, not class attributes or globals. Opening arguments come from `ctx.arguments`, an immutable snapshot; constructor arguments are not supported. `on_open`, `on_close`, `visible`, `render` and completions all execute on the GUI thread.

The host owns outer Begin/End, ID scoping and cleanup. Do not surround `render` with another outer `imgui.begin/end`. Balance every nested widget scope, including exception paths. Use upstream scoped/context-manager APIs only after checking their installed ImGui-Bundle signatures; mkapp does not provide a second widget abstraction.

The GUI submission surface is:

```text
ctx.submit(target, primitive_payload,
           on_success=callback, on_error=callback,
           timeout_ms=5000) -> request_handle
request_handle.cancel() -> None
```

Both callbacks are required. Submission itself can raise `AppError` for invalid input, pressure or a closed owner; catch it to restore pending UI state, as the complete example does. Later errors arrive at `on_error`. Cancellation is best effort. Do not wait on the handle or depend on an internal Future. There is no public `GuiRequest` class import in `mkapp.sdk`; use only the returned handle's cancellation behavior.

Closing invalidates callbacks, including callbacks used to clear a busy flag; that is safe because the window is gone. For multiple overlapping requests on a still-live window, maintain a local request sequence and ignore older completions, or admit only one request at a time. Owner/window lifetime checks do not imply query-order freshness.

`open_on_start=True` works with all optional extensions off. Dynamic action-based opening currently requires `tiles` and an explicit grant for `mkapp.windows.open.v1`:

```python
ctx.submit(
    "mkapp.windows.open.v1",
    {
        "factory_id": "myapp.viewer.main.v1",
        "arguments": {},
        "placement": {
            "anchor_window": ctx.instance_id,
            "side": "below",
            "ratio": 0.4,
        },
    },
    on_success=lambda result: None,
    on_error=lambda error: None,
)
```

Use the registered window class ID for `factory_id`; native pointers/dock node IDs are never inputs. Sides are `left`, `right`, `above`, `below`, `tab`. Stale/floating anchors fall back to root placement; the result includes `instance_id` and `fallback`. Cross-plugin opens also require a declared dependency on the window owner. Strict-anchor policy and persistent layouts are not available.

Reusable renderer registration and invocation:

```python
def render_badge(data, emit) -> None:
    from mkapp.sdk import imgui_bundle

    if imgui_bundle.imgui.button(data["label"]):
        emit({"kind": "clicked"})

# In Plugin.prepare:
ctx.components.register("myapp.viewer.badge.v1", render_badge)

# In Window.render, with a WindowContext:
ctx.components.render(
    "myapp.viewer.badge.v1", "refresh_button",
    {"label": "Refresh"}, lambda intent: self.refresh(ctx),
)
```

These are insertion fragments for the provider/viewer example. The renderer is synchronous, receives immutable non-sensitive view data, and emits intents through the caller's callback. Host ID scopes distinguish window/component/instance; use distinct instance strings for repeated components. Cross-plugin consumers declare the renderer owner's dependency. Components are local GUI calls, not an alternative route for plugin business communication or hidden resource ownership.

## Managed workers

Register named importable jobs during `prepare`; submit them from a lifecycle hook or action. The name is owner-prefixed, such as `myapp.prices.calculate`. Kinds are explicit: `async`, `thread`, `process`. The host does not infer parallelism.

For a CPU job, add `myapp_prices/jobs.py`:

```python
def sum_squares(payload: dict) -> dict:
    count = payload["count"]
    if type(count) is not int or not 0 <= count <= 100_000:
        raise ValueError
    return {"total": sum(i * i for i in range(count))}
```

Registration and submission fragments:

```python
# Plugin.prepare:
ctx.workers.register(
    "myapp.prices.calculate", "myapp_prices.jobs:sum_squares",
    kind="process", max_concurrency=1,
)

# Inside an async lifecycle hook or Action.handle:
result = await ctx.workers.submit(
    "myapp.prices.calculate", {"count": 1000}, timeout_ms=5000,
)
```

Only the owning plugin can submit its worker; other plugins call its public action. Jobs receive one primitive payload and return primitive results, not dataclasses or contexts. Process jobs must be importable module-level synchronous functions, never lambdas/closures/bound plugin methods. Keep app execution under `if __name__ == "__main__"` for spawn. An `async` job must be an async function; thread/process jobs must be synchronous.

Worker submission is not an Action registration: wrap it with a validated action when exposing it to a window/other plugin. Do not pass a GUI context, socket, native connection, sensitive value or private handle into workers. Ordinary serialized bytes are not encryption or a sandbox.

Thread jobs need explicit I/O timeouts and bounded computation. A timed-out underlying job can continue and retains admission until it actually completes. Process/owner shutdown is bounded; arbitrary trusted Python/native threads cannot be forcibly stopped. Do not retry a side effect just because a caller timed out.

## Events

Register a versioned event contract with a synchronous validator during `prepare`. Subscribe async callbacks explicitly. `@on` attaches metadata only and never activates a subscription by itself.

```python
from mkapp.sdk import BaseEvents, Plugin, PluginContext, on


class Observer(Plugin):
    @on(BaseEvents.START)
    async def app_started(self, payload) -> None:
        # Short, non-blocking observation; required initialization belongs in start.
        pass

    def prepare(self, ctx: PluginContext) -> None:
        ctx.events.subscribe(self.app_started)
```

Equivalent explicit form: `ctx.events.subscribe(BaseEvents.START, self.app_started)`. A bound callback accepts exactly one payload argument and is async. All prepare-time subscriptions are validated and acknowledged before readiness. Custom cross-plugin event subscriptions need appropriate app grants and a provider dependency when required.

Publish a custom event only after registering its validator: `await ctx.events.publish("myapp.prices.changed.v1", {"revision": 3})`. Events carry ordinary primitive values, not dataclass objects automatically converted by `@contract`.

Host-only base events are `BaseEvents.START` (`mkapp.app.start.v1`), `SHUTDOWN` (`mkapp.app.shutdown.v1`) and `KILL` (`mkapp.app.kill.v1`). Plugins must not publish host lifecycle events. Best-effort delivery is not a durability guarantee; shutdown/kill observations may be missed. Put required cleanup in `stop` and owned disposal, never solely in an event handler. Use authoritative snapshots/revisions to recover after dropped notifications.

## State, cache and local storage

All three store **non-sensitive primitive values only**. Register keys/namespaces during `prepare`, guard optional services, and validate application shapes. Validators return accepted/normalized values or raise; `lambda value: value` is suitable only for deliberately unrestricted primitive fixtures.

```python
from mkapp.sdk import NamespaceSpec, StateSpec


def bounded_count(value):
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValueError
    return value

# In Plugin.prepare:
if ctx.features.has("state"):
    ctx.state.register(StateSpec(
        "myapp.prices.count", bounded_count,
        persist=ctx.features.has("state_persistence"),
    ))
if ctx.features.has("cache"):
    ctx.cache.register(NamespaceSpec(
        "myapp.prices.samples", bounded_count,
        max_entries=32, max_bytes=65536, ttl=30.0,
    ))
```

`StateSpec(key, validator, readers=(), writers=(), persist=False, schema_version=1)` declares a registered state key. `NamespaceSpec(id, validator, schema_version=1, max_entries=128, max_bytes=1048576, ttl=60.0)` declares cache/storage value validation and budgets. Cache TTL is a namespace setting, not a per-put option. Storage has no cache TTL eviction behavior.

Actual facade usage on a **PluginContext**:

```text
await ctx.state.get(key) -> {found, value, revision}
await ctx.state.set(key, value, expected_revision=None) -> {found, value, revision}
await ctx.state.delete(key, expected_revision=revision) -> {found, value, revision}
await ctx.cache.get(namespace, key) -> {hit, value}
await ctx.cache.put(namespace, key, value) -> {}
await ctx.cache.delete(namespace, key) -> {}
await ctx.local_storage.get(namespace, key) -> {found, value}
await ctx.local_storage.put(namespace, key, value) -> acknowledgement
await ctx.local_storage.delete(namespace, key) -> acknowledgement
```

`Records` is an exported shared protocol; its generic `get(namespace, key)` signature does not describe the implemented single-key state convenience call. Use the service-specific call forms above. There is no state `.put(...)` or cache `.set(...)` operation. Do not depend on unspecified storage acknowledgement fields.

From an **ActionContext**, use these wire routes directly; they retain validation and ownership checks:

| Route | Payload |
| --- | --- |
| `mkapp.state.get.v1` | `{"key": "myapp.prices.count"}` |
| `mkapp.state.set.v1` | `{"key": "myapp.prices.count", "value": 1, "expected_revision": 0}` |
| `mkapp.state.delete.v1` | `{"key": "myapp.prices.count", "expected_revision": 1}` |
| `mkapp.state.snapshot.v1` | `{}`; returns a key-to-snapshot map filtered to readable keys |
| `mkapp.cache.get.v1` / `delete.v1` | `{"namespace": "myapp.prices.samples", "key": "demo"}` |
| `mkapp.cache.put.v1` | Same namespace/key plus `"value": 8` |
| `mkapp.local_storage.get.v1` / `delete.v1` | `{"namespace": "myapp.prices.presets", "key": "demo"}` |
| `mkapp.local_storage.put.v1` | Same namespace/key plus a validated `"value"` |

For example: `await ctx.actions.call("mkapp.state.get.v1", {"key": "myapp.prices.count"})`. Optional service availability must be established by the owning plugin; an action has no `ctx.features`. Record-service routes have host routing access, with ownership enforced by their providers. Additional cross-plugin state access requires `StateSpec.readers`/`writers`; cache and storage namespaces are owner-only. Other plugins consume an owner's public data action instead.

State is authoritative and single-writer. Use `expected_revision` for read/modify/write conflict detection; a mismatch raises `conflict`. Missing values and stored null differ by `found`; missing initial keys have revision zero. Delete/recreate does not reset session revisions. State change events (`mkapp.state.changed.v1`) contain key/revision hints; recover through a snapshot, not event replay. Persistence is explicitly enabled per key and requires `state_persistence` plus `state`; unsupported schema formats are refused, not silently reset.

Cache stores recomputable values only. Use `hit` to distinguish a stored null from a miss. TTL is monotonic, reads do not extend it, and entry/byte pressure can evict values. A miss must leave the app able to recompute. Namespace teardown clears its entries.

Local storage requires the optional DuckDB dependency and `local_storage` extension. One host storage thread owns the native connection; no raw SQL/connection API is exposed. Writes acknowledge after commit. Timeouts can leave write outcome uncertain: reconcile after outstanding work settles instead of blindly retrying. Durable records survive owner shutdown; registration/schema compatibility still applies on reopen. There are no user-defined migration callbacks or physical on-disk quota guarantees yet. Never select plaintext storage as a fallback for sensitive data.

## Commands and completion

Commands are palette metadata targeting registered action IDs; they do not bypass validation or grants. Register them only when `palette` is enabled.

```python
from mkapp.sdk import CommandField, CommandSpec

# In myapp.prices Plugin.prepare, after registering Sample:
if ctx.features.has("palette"):
    ctx.commands.register(CommandSpec(
        id="myapp.prices.command.v1",
        path=("prices", "sample"),
        action="myapp.prices.sample.v1",
        fields={"count": CommandField("integer", required=False, default=8)},
        description="Request a bounded synthetic sample",
    ))
```

The application grants `myapp.prices.sample.v1` to caller `mkapp.palette`; the command owner's grant does not substitute for the palette caller's grant. Command path and ID conflicts are rejected. Grammar is a bounded command path plus named `key=value` fields, such as `prices sample count=8`. Quoted strings use JSON escaping. No positional expression language, imports, function evaluation, `eval` or `exec` is supported.

`CommandField(kind, required=True, default=None, choices=(), nullable=False)` supports `string`, `integer`, `number`, `boolean`, `enum`. Optional fields need a valid typed default; enum choices are strings. Field parsing is not a replacement for the action's range/shape validator. Inline sensitive fields and raw persisted history are unavailable. Palette works without autocomplete; disabling palette should leave the plugin's core behavior usable.

Autocomplete requires both `palette` and `autocomplete`. Register an action normally, then register its ID with `ctx.completions.register(action_id)` when the feature is present. Grant that action ID to `mkapp.palette`. The current request is `{"text": str, "cursor": int}` and the response is a **bare list** of records with exactly `{text, start, end, label}`. Offsets are Python character indices, not UTF-8 byte offsets, satisfying `0 <= start <= end <= len(query_text)`; insertion text is at most 256 characters. At most 20 suggestions survive the palette's filtering/deduplication. Provider calls use a 250 ms timeout after a 100 ms debounce; return a small bounded result quickly.

An annotated action requires a dataclass response, so it cannot directly serve this bare-list response contract. Use a handwritten `Action` with explicit validators. This example offers one insertion and can be added to `myapp.prices`:

```python
from mkapp.sdk import Action, ActionContext


def completion_query(value):
    if type(value) is not dict or set(value) != {"text", "cursor"}:
        raise ValueError
    if type(value["text"]) is not str or len(value["text"]) > 4096:
        raise ValueError
    if type(value["cursor"]) is not int or not 0 <= value["cursor"] <= len(value["text"]):
        raise ValueError
    return value


def completion_results(value):
    if type(value) is not list or len(value) > 20:
        raise ValueError
    for item in value:
        if type(item) is not dict or set(item) != {"text", "start", "end", "label"}:
            raise ValueError
        if type(item["text"]) is not str or len(item["text"]) > 256:
            raise ValueError
        if type(item["label"]) is not str or len(item["label"]) > 256:
            raise ValueError
        if type(item["start"]) is not int or type(item["end"]) is not int:
            raise ValueError
        if not 0 <= item["start"] <= item["end"] <= 4096:
            raise ValueError
    return value


class CompleteSample(Action):
    id = "myapp.prices.complete.v1"
    input = staticmethod(completion_query)
    output = staticmethod(completion_results)
    timeout_ms = 250

    async def handle(self, payload: dict, ctx: ActionContext) -> list[dict]:
        text, cursor = payload["text"], payload["cursor"]
        suggestion = "prices sample count=8"
        if not suggestion.startswith(text[:cursor]):
            return []
        return [{"text": suggestion, "start": 0, "end": cursor, "label": suggestion}]

# In Plugin.prepare:
if ctx.features.has("autocomplete"):
    ctx.actions.register(CompleteSample)
    ctx.completions.register(CompleteSample.id)
```

The palette checks each replacement against the actual current query length and ignores stale replies. A provider must still validate its own input/output and bound work. See [the current palette adapter](../src/mkapp/builtin/palette.py) when extending this version-specific integration. There is no durable suggestion state or guarantee that a cancelled provider stopped executing.

## Private operations and credentials

The direct `ctx.private` API is available on **PluginContext**, not ActionContext or WindowContext. It is an intentional exception to business messaging through actions. It uses a separate private executor and host-held opaque handles.

```text
ctx.private.hold(SecretValue(value)) -> PrivateHandle
ctx.private.grant(handle, grantee, operation, *, ttl=30) -> PrivateHandle
ctx.private.register(identifier, callback, *, safe_result=False) -> Registration
await ctx.private.invoke(operation, handle) -> PrivateHandle or explicitly safe result
await ctx.private.credential(name) -> PrivateHandle
await ctx.private.set_credential(name, SecretValue(string)) -> None
```

Operation IDs are versioned and owner-prefixed; credential names are namespaced IDs. Private callbacks are synchronous, accept exactly one `SecretValue`, and run off the GUI/runtime in the private executor. Handles bind owner, grantee, operation, expiry and lifecycle generation. Cleanup, expiry and host revocation invalidate them, with a second validity check before releasing results. A grant is not an action-serializable token.

Synthetic lifecycle recipe:

```python
from mkapp.sdk import Plugin, PluginContext, SecretValue


def synthetic_length(value: SecretValue) -> dict:
    return {"length": len(value.reveal())}


class PrivateDemo(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.private.register(
            "myapp.private_demo.length.v1", synthetic_length, safe_result=True,
        )

    async def start(self, ctx: PluginContext) -> None:
        handle = ctx.private.hold(SecretValue("SYNTHETIC"))
        result = await ctx.private.invoke("myapp.private_demo.length.v1", handle)
        assert result == {"length": 9}
```

This is a class fragment; a directory plugin still needs its descriptor and factory. `safe_result=True` is an explicit declassification decision; use it only for a schema whose output is safe. Default private-operation results remain handles. Never return the handle or revealed bytes in an ordinary action result to bridge into a window. A declared safe result still must not contain sensitive data; primitive validation cannot determine business sensitivity.

Credential operations validate an actual OS backend and fail closed when unavailable/unsuitable. There is no plaintext/null fallback. Shared interpreters can access credentials under the tested OS trust model. Raw wrapper copies can defeat cooperative rejection. Python/native widgets do not guarantee zeroization. Large sensitive documents need an approved encrypted backend, which is not currently supplied by this SDK.

There is no supported direct private GUI prompt bridge or general public cross-plugin handle-delivery API in the current surface. Do not transport a handle in an event/action or call another plugin's internals to wire one up. Such features need an explicit host contract. There is also no public `ctx.private.revoke(...)` method; do not confuse host cleanup mechanisms with plugin methods.

## Configuration, extensions and packaging

Application bootstrap uses `from mkapp import AppConfig, Limits, run`. `run(config, *, smoke_frames=None, relaunch=None)` owns GUI/runtime lifetime. Set an explicit `app_root` for predictable embedding. `relaunch` is an application callback after shutdown, not a plugin hot-reload hook.

`AppConfig` fields are `app_id`, `title`, `app_root`, `plugin_roots`, `approved_distributions`, `enabled_plugins`, `toggles`, `grants`, `extensions`, `data_root`, `safe_mode`, `limits`. Paths are `pathlib.Path`; approved roots/distributions and enabled plugins are tuples. Grants map caller IDs to tuples of exact action/event IDs. Do not assume wildcard grants.

Omitted/`None` `enabled_plugins` selects eligible discoveries; an explicit tuple is an allowlist; `()` selects none. Saved toggles apply within that selection. Required dependencies excluded by selection are blocked, not auto-enabled. Safe mode imports no application plugins. Desired enablement and actual lifecycle status remain separate; toggles require restart. No physical Python module unload is promised.

Optional extensions default to off:

| Extension | Use and dependencies |
| --- | --- |
| `tiles` | Dynamic relative window opening/docking; basic floating windows exist without it |
| `palette` | Command UI; works without tiles |
| `autocomplete` | Separate completion extension; requires palette |
| `plugin_manager` | Private management UI; works independently of registry announcements |
| `registry_announcements` | Opt-in public revisioned plugin snapshots/events; descriptor `announce` also matters |
| `state` | Non-sensitive revisioned keys |
| `state_persistence` | Explicitly persistable state; requires state |
| `cache` | Recomputable session values; independent of state/storage |
| `local_storage` | Durable owner records; requires installed DuckDB extra, independent of state/cache |

For example, `extensions=("palette", "autocomplete", "cache")`. Feature checks use the effective services after startup failures; configured availability alone does not prove a provider initialized. Declare `required_features = ["cache"]` if the plugin cannot operate without it; otherwise guard registration and offer a functional disabled path.

### Registry and management actions

These optional host actions use the ordinary `ctx.actions.call`/`WindowContext.submit` route and require exact caller grants. Prefer the built-in management UI unless the application specifically needs custom management behavior.

| Route | Request and result |
| --- | --- |
| `mkapp.registry.snapshot.v1` | `{}` -> `{revision, plugins}`; plugin records contain `id`, `version`, `status` and include only descriptors with `announce=true` |
| `mkapp.manager.snapshot.v1` | `{}` -> `{plugins, can_restart, installer_available}`; private records contain `id`, `version`, `desired`, `status`, `error`, `pending_restart` |
| `mkapp.manager.propose.v1` | `{"toggles": {"myapp.viewer": false}}` -> `{toggles, restart_required}`; expands dependency effects within the app allowlist without committing |
| `mkapp.manager.commit.v1` | Same toggles payload; revalidates, atomically saves desired preferences, returns the proposal; running code is unchanged |
| `mkapp.manager.restart.v1` | `{}` -> `{"restart_requested": true}` when an application relaunch callback is configured; otherwise `feature_unavailable` |

The registry routes/events require `registry_announcements`; manager routes require `plugin_manager`. Registry events `mkapp.registry.ready.v1`, `stopping.v1`, `stopped.v1` and `failed.v1` carry `{id, revision}`. Obtain a snapshot and reconcile revisions rather than treating announcements as complete inventory. Opt-out plugins remain in the host's private inventory. Management does not install packages, widen the application allowlist or unload running modules. Treat a toggle commit and a restart as explicit user-facing changes, not incidental plugin setup.

### Installed wheels

Wheel plugins use the same descriptor and SDK. The entry-point name equals the plugin ID; its target equals descriptor `entry`:

```toml
[project.entry-points."mkapp.plugins"]
"myapp.prices" = "myapp_prices:create_plugin"
```

Place the descriptor under the entry package for wheel distribution and ensure the build includes it. The application explicitly approves the installed distribution through `approved_distributions`. Static distribution ownership is validated before entry-point import. One plugin per distribution is the current model. Do not confuse the Python distribution name with plugin ID or package name. Use [the wheel fixture](../examples/wheel/pyproject.toml) as the packaging reference. Installation remains external to runtime resolution.

## Errors and troubleshooting

Handle `AppError.code`; do not log payloads, raw exception messages, arbitrary paths or sensitive titles. Wire errors use fixed allowlisted codes; custom exception text is sanitized and arbitrary custom codes generally become `internal_error`.

| Symptom/code | Check or response |
| --- | --- |
| Plugin absent | Correct runtime root, immediate child layout, descriptor/package, selection, saved toggles, safe mode and approved sources |
| `invalid_metadata` | Descriptor field names/types, SDK range, entry target, contained package paths and ID rules |
| `duplicate_plugin` | Duplicate plugin IDs or exported import packages; rename explicitly |
| `invalid_registration` | Required async/sync override, class metadata, constructors, schema support, namespaces, duplicate registration or disabled persistence |
| `permission_denied` | Actual caller grant, service ownership, state readers/writers, component/window dependency or expired private handle |
| `feature_unavailable` | Effective extension state, failed backend, missing optional dependency or unsupported credential backend |
| `unknown_action` / `not_ready` | Exact versioned ID, successful provider registration/start and dependency ordering |
| `invalid_payload` / `invalid_message` | Exact primitive types, map fields, bounds, response schema and encoded limits |
| `busy` | Admission pressure; bound/coalesce work and expose a retry opportunity without automatic side-effect retries |
| `timeout` / `cancelled` | Outcome may be uncertain; do not assume work stopped or a side effect did not occur |
| `owner_failed` | Lifecycle failure/closed window or owner; discard stale work |
| `conflict` | Refresh authoritative state and explicitly reconcile/reapply the intended change |
| `internal_error` | Inspect safe diagnostics and narrow synthetic reproductions; do not expose raw exceptions |

Synchronous GUI submission errors and asynchronous callback failures are separate paths. A successful headless test does not prove native rendering, docking, IME behavior or OS credential correctness.

## Verification and delivery checklist

For this repository, current standard checks are:

```sh
uv run --extra duckdb pytest -q
uv run mypy
uv run ruff check src tests examples experiments
uv run ruff format --check src tests examples experiments
```

Choose focused tests proportional to changes, then required broader checks. Do not add tests that merely reproduce implementation details. Useful public-boundary evidence includes:

- Metadata-first discovery, inherited no-op hooks, invalid factory result, valid startup and cleanup after partial failure.
- A real encoded action round trip, malformed request/response rejection, explicit caller grants and bounded pressure/timeouts.
- Adding/disabling the new consumer while the provider/unrelated plugins are unchanged; default, explicit and empty selections.
- Worker completion/cancellation/late result and cooperative resource shutdown; real spawned process when CPU work is added.
- Optional features enabled and disabled; missing mandatory services blocked before import.
- Multiple window instances, frame-boundary callbacks, stale completion handling and main-thread rendering.
- Synthetic private sentinel rejection and fail-closed backend behavior when touching private APIs.
- Real durable reopen/commit/rollback for storage changes, not just an in-memory mock.

Native probes are explicit opt-ins:

```sh
uv run python experiments/plugin_cli_smoke.py --native
uv run --extra duckdb python experiments/native_smoke.py
```

Use synthetic values only. Credential probes write/delete OS records and must be deliberately selected; they are not ordinary unit tests. Report the exact checks executed and limits: native macOS evidence does not prove Windows/Linux behavior. For generated app/plugin code, test it in the application's own environment as well as against framework tests when applicable.

Before finishing an SDK task, verify:

- All framework imports in plugin code use `mkapp.sdk`; no provider-internal imports or private host dependencies.
- Descriptor IDs, package, factory, contract versions and declared dependencies match code.
- Required grants and extension choices live in explicit app configuration.
- No class-reference shortcut bypasses the bus; both input and output are validated and bounded.
- Render/constructors/hooks are synchronous and non-blocking; no hidden executors or context transfer.
- UI state belongs to each window and invocation state stays local to each action call.
- Resources have bounded ownership/cleanup; optional disabled paths still function.
- Sensitive values stay on the direct private path; no payload/history/log capture was introduced.
- Existing tests/user work remain intact; documentation describes actual implemented behavior.

## Unavailable APIs and known limits

Do not generate code assuming any of these exist:

- Hot loading/unloading, file watching/source reload or plugin marketplace/download resolution.
- An untrusted-plugin security sandbox, process-memory isolation or guaranteed secret zeroization.
- Generic IoC/service lookup, constructor injection, automatic subclass discovery, `WindowSpec`, or separate callable-handler registration.
- `ctx.gui`, `ctx.bus`, `ctx.tasks`, `ctx.scope`, `ctx.config`, `ctx.logger`, `ctx.storage`, `ctx.windows.open(...)`, or `ctx.workers.run(...)`.
- Direct state/private/events/features fields on `ActionContext`; runtime services on `WindowContext`.
- Automatic dataclass serialization at arbitrary bus/worker call sites or dataclass results returned to consumers.
- Automatic workload selection, a cooperative worker stop-token API, progress streaming or forced thread cancellation.
- Direct private GUI prompts, encrypted-document persistence, a plugin credential-backend switch or plaintext fallback.
- Strict docking-anchor policy, safe persisted layouts, configurable palette placement or complete IME/middle-of-escape completion acceptance.
- Raw SQL/native storage connections, cross-key transactions, user-supplied storage migrations or durable event replay.
- Guaranteed lifecycle-event delivery, automatic retries, implemented arbitrary provider replacement or a managed package installer.

The public `imgui_bundle` export is the original upstream module, lazily loaded, not an SDK GUI wrapper. Check current upstream docs for new widget APIs; keep version-sensitive docking in the host adapter. Framework completion criteria remain incomplete even though the examples and authoring CLI run.

For executable references start with [the market application](../examples/market/app.py), [workbench](../examples/workbench/app.py), [SDK exports](../src/mkapp/sdk/__init__.py), and [SDK declarations](../src/mkapp/sdk/core.py). Source links are inspection references; they do not authorize plugins to import private implementation modules.
