# mkapp architecture

Status: v0.1 target contracts, with an initial implementation. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the implemented subset, compatibility evidence and outstanding acceptance requirements. Do not infer runtime support from a section of this target design alone.

## 1. Purpose and boundaries

mkapp is a small Python framework for building ad hoc data-science and trading desktop apps on ImGui-Bundle. Start with a calculation, dataset view or market monitor and add features as the workflow evolves. Add a trusted local feature by placing its plugin in the app's `plugins/` directory and restarting. Plugins may be headless and can declare dependencies on other plugins. Adding a feature requires no edits to mkapp or to existing plugins whose contracts it already uses.

The host owns lifecycle, routing, scheduling, configuration and the GUI frame. Optional first-party extensions provide plugin announcements, management UI, docking, command palette, autocomplete, global state, an in-memory cache and DuckDB-backed local storage. Secure storage is a mandatory host service. An empty application can run with all optional extensions disabled.

Minimalism means one host, one SDK package, one action protocol, one extension mechanism and one rendering context. The smallest feature can be one module and a descriptor. Register only the actions or windows it contributes, and keep calculations and other implementation details as ordinary Python within their owning plugin.

### Composing a data app

- A data plugin owns its data source and representation. Keep full datasets and DataFrames with that owner. Expose bounded, versioned actions for aggregates, pages or plot-ready samples. Use the existing worker SDK when computation or I/O could block the runtime.
- An analysis or UI plugin can reuse those contracts without importing provider internals. A UI plugin owns its window state and submits actions asynchronously. It can render snapshots with the upstream ImGui-Bundle API.
- Namespaced registrations and per-plugin resource scopes let features coexist. Reject duplicate registrations with a clear diagnostic. Declare actual dependencies and keep optional integrations behind feature checks so unrelated plugins still work when a feature is absent.
- Dataset schemas, indicators, strategies and broker integrations belong to application plugins. The framework supplies composition and execution boundaries.

### Core decisions

| Concern | Decision |
| --- | --- |
| Rendering | ImGui-Bundle with HelloImGui; all ImGui and graphics calls on the main thread |
| Background work | One background asyncio runtime thread; SDK-managed named thread/process workers |
| Plugin communication | ZeroMQ ROUTER/DEALER action bus, MessagePack payloads, in-process endpoints initially |
| Plugin loading | App-local `plugins/`, additional approved directories and approved wheel entry points, with metadata validated before plugin import |
| Lifecycle | Deterministic startup and reverse shutdown; enable/install changes require restart |
| Dependency resolution | Validate installed plugin versions and a dependency DAG; never download dependencies during startup |
| Extension mechanism | Typed registrations with ownership and disposable handles; built-ins use the same mechanism |
| SDK object model | Shallow `Plugin`, `Window` and `Action` base classes with explicit registration and host-managed instances |
| Sensitive data | Direct scoped sensitive-data service, excluded from the ordinary bus, global state, cache and local storage |
| Trust | All imported plugins are trusted arbitrary Python code; SDK permissions are cooperative controls |
| Persistence | Small versioned atomic configuration files, optional state snapshots and opt-in DuckDB storage for plugin-owned records |

## 2. Requirements interpreted explicitly

“Asynchronous GUI” means background operations never block the frame loop. It does not mean concurrent calls into the same ImGui context. Rendering callbacks are synchronous, short and main-thread-only; background results arrive as immutable snapshots.

“Automatic parallelization” means mkapp manages dispatch, worker lifetime, cancellation, limits and result delivery after the plugin declares the workload. It cannot infer whether arbitrary Python functions are thread-safe or CPU-bound. Async is concurrency; ordinary CPython threads do not guarantee parallel execution of Python CPU work. CPU work uses explicit process workers.

“Absolutely secured” is a security objective, not a promise that Python can enforce against malicious in-process plugins, a compromised account or OS, screenshots, swap, debuggers or physical access. The initial trust model supports reviewed plugins only. Running untrusted plugins safely requires a separately designed OS sandbox and restricted UI protocol; that is outside v0.1.

The prompt references expression fields but supplies no syntax or examples. Section 10 defines a deliberately small proposed grammar. It is a design default, not a recovered requirement.

## 3. Package shape and dependency direction

Proposed source layout; create modules only when their implementation is needed:

```text
src/mkapp/__init__.py
src/mkapp/__main__.py
src/mkapp/sdk/                 Plugin/Window/Action bases, contexts, protocols, records, handles
src/mkapp/runtime/             host, config, discovery, lifecycle, bus, workers, privacy
src/mkapp/ui/                  HelloImGui adapter, frame bridge, window host
src/mkapp/builtin/             optional first-party extensions
examples/                     minimal headless/UI/dependency/worker plugins
tests/                        focused unit, integration and GUI smoke checks
```

Dependency direction: first-party and third-party plugins depend on `mkapp.sdk`; runtime implements SDK protocols; UI adapts ImGui-Bundle to SDK registrations. SDK import must not create sockets, initialize a GUI, read secrets or start threads. Plugin-independent SDK definitions should import without loading ImGui-Bundle. No plugin imports another plugin's implementation or `mkapp.runtime`/`mkapp.ui` internals.

GUI plugins can explicitly import `imgui_bundle` from `mkapp.sdk`. The SDK lazily returns the original upstream module, so plugins call its widgets directly and headless SDK imports avoid loading it. mkapp tests against the selected ImGui-Bundle version. The host still owns GUI initialization, the frame loop and docking, and widget calls remain on the GUI thread.

Use OOP for entities with identity, behavior and ownership: plugins, windows and executable actions. Keep one shallow public base class per entity. Use standard-library dataclasses for configuration, results, placement and command specs, and small Protocols for service/provider boundaries; do not force every record into a mutable class hierarchy. Candidate runtime dependencies are `imgui-bundle`, `pyzmq`, `msgpack`, `packaging` and a vetted OS credential-store adapter such as `keyring`. Do not introduce Pydantic, a parser framework or a plugin framework without demonstrated need. Select and lock tested versions during implementation rather than copying unverified version pins into this design.

## 4. Host configuration and extension model

An application supplies `AppConfig` with app ID and title. Optional settings include an application root override, additional plugin roots, approved installed distributions, a plugin allowlist, extension switches, bus/worker limits, palette shortcut and placement, persistence locations and third-party policy. Application identity namespaces credentials, configuration, layout, state, cache and local storage. Resolve relative paths against the application root, never an incidental working directory.

Configuration precedence: built-in defaults < application configuration < persisted user preferences < explicit launch overrides. Security policy is an application-owned ceiling: user preferences and plugin configuration cannot enable forbidden third-party installation, plaintext credentials, arbitrary network bus endpoints or diagnostic payload logging. Validate configuration before importing plugins.

Every configurable core facility has an SDK protocol and typed options. Plugins contribute registrations; applications select singleton implementations at bootstrap. Provider conflicts are errors unless the application explicitly chooses a provider. A plugin cannot replace a running bus, security service or scheduler halfway through a session. Safety invariants remain mandatory for replacement providers.

| Facility | SDK extension/configuration surface |
| --- | --- |
| Discovery and dependency resolver | Bootstrap-selected discovery sources and resolver protocol; approved roots and version policy |
| Registry announcements | Metadata contributors, startup snapshot, opt-in subscriptions and publication policy |
| Plugin management | List columns/details, management commands and installer provider; application policy controls availability |
| Windows and tiles | Registered `Window` subclasses, placement requests, bootstrap layout provider and layout options |
| Palette | Commands, typed fields, validators, permitted field types, presentation options and parser provider |
| Autocomplete | Independently enabled ordered completion providers |
| Bus | Versioned actions, subscriptions, schemas, metadata-only diagnostics and bootstrap transport adapter |
| Events | Exact subscriptions, optional `@on` declarations and host-published `BaseEvents` |
| Workers | Named job registrations, executor provider and limits |
| Sensitive data | Mandatory security contract, bootstrap-approved credential backend and private operation registrations |
| Global state | Namespaced key schemas, read/write grants and optional persistence provider |
| Cache | Owner-scoped namespace schemas, TTL and capacity options, bootstrap-selected provider |
| Local storage | Owner-scoped record schemas and quotas, bootstrap-selected provider, optional `mkapp[duckdb]` dependency for the built-in provider |
| Components | Versioned component factories, themes and resource registrations |

Registration returns an idempotent `Registration.close()` handle. The host owns a per-plugin resource scope and closes all registrations, subscriptions, tasks and windows during failure/shutdown. Registrations identify their owner, stable ID and API major version. Reject conflicting IDs; no last-import-wins behavior or monkey-patching.

Plugins register implementation classes explicitly through `ctx.actions.register(CalculateAction)` and `ctx.windows.register(ComputeWindow, open_on_start=True)`. For actions, `@action(id)` can supply the subclass from an annotated async function. Both forms use the same registration path. The host checks the subclass and required metadata, snapshots the registration metadata, then constructs instances in the correct execution domain. Registration does not instantiate a window or run an action. There is no registration by import, subclass scanning, implicit context injection or required superclass call.

`AppConfig.extensions` selects optional framework features: `registry_announcements`, `plugin_manager`, `tiles`, `palette`, `autocomplete`, `state`, `state_persistence`, `cache` and `local_storage`. Omitting it is equivalent to `extensions=()`. Application plugins are selected separately through discovery and `enabled_plugins`. Autocomplete requires palette and state persistence requires state. Cache and local storage can each run independently of the other and of state. Plugin manager and palette work without tiles. Disabled optional services return `feature_unavailable` through their SDK facade.

`ctx.features.has(name)` lets plugins guard optional contributions. A descriptor may declare `required_features` for host services it cannot work without; the resolver blocks that plugin before import if those services are disabled. Optional command contributions must not make an otherwise headless plugin depend on the palette. The basic window host is always available in desktop mode; tile management is its optional layout extension.

## 5. Plugin discovery, metadata and dependencies

### Static descriptor

Use one `mkapp-plugin.toml` descriptor for both directory and wheel plugins. Illustrative fields:

```toml
manifest_version = 1
id = "example.notes"
version = "0.1.0"
sdk = ">=0.1,<0.2"
entry = "example_notes.plugin:create_plugin"
announce = true
required_features = []

[requires]
"example.documents" = ">=1.0,<2.0"

[optional]
"example.search" = ">=1.0,<2.0"
```

IDs are stable lowercase dotted ASCII identifiers. Versions and specifiers follow `packaging` conventions. The descriptor also supports declared capabilities, descriptive metadata and separately versioned provided component/action contracts. Validate field types, size, allowed values, source identity and canonical paths before execution. `announce` affects public registry visibility only; the host always maintains its private lifecycle inventory.

**Development directories:** at startup, look for `plugins/` next to the app entry script, normally `app.py`. An explicit `AppConfig.app_root` overrides that location. This source yields no candidates if the app root is unknown or the folder is missing. Inspect only immediate child directories containing `mkapp-plugin.toml` and an importable, uniquely named package. The application-owned `plugins/` folder is an approved source by convention and must contain only trusted code. `AppConfig.plugin_roots` can add individually approved directories, each with its own descriptor. Discovery reads metadata before importing code. Canonicalize paths, reject symlink escapes and namespace shadowing, and reject two roots exporting the same import package. If the loader adds a root to `sys.path`, it does so once after approval and never changes it per callback.

**Installed wheels:** discover only approved installed distributions in the app's environment. Distributions advertise `[project.entry-points."mkapp.plugins"]`. The entry-point name equals the plugin ID and its target equals descriptor `entry`. Locate the descriptor under the entry target's package using distribution file metadata without calling `EntryPoint.load()`. Require that the descriptor and entry package belong to that distribution. Wheel builds must include this resource. Missing or mismatched metadata is an error. One plugin per distribution initially keeps this mapping small and deterministic.

Python distribution dependencies belong in wheel `Requires-Dist`; plugin runtime dependencies belong in the descriptor. These are different graphs. Resolve neither by importing plugins nor by installing packages during startup.

**Local authoring:** `mkapp plugin create <plugin_id>` generates a directory plugin with the `empty`, `ui` or `data` starter selected by `-t`/`--template`. It always prefixes the relative ID with the application name, inferred from the selected root's `[project].name` or directory name, with an explicit `--app-name` override. Authoring defaults to the current directory and accepts `--app-root`; this does not change runtime root inference. Generation reads static metadata, validates names and conflicts, refuses overwrites and publishes the descriptor only after the package is ready. It never imports plugins, initializes GUI services, installs dependencies or modifies application selection/grants. Activation uses ordinary discovery at restart. See [EXAMPLES.md](EXAMPLES.md#create-a-plugin).

### Resolution

1. Discover static descriptors from approved sources; disabled plugins are not imported.
2. Reject duplicate plugin IDs, malformed metadata, unsupported manifest/SDK versions and source collisions.
3. Select desired plugins using the defaults or explicit allowlist below. Required disabled, missing or incompatible dependencies block a plugin with an explanation. Dependency resolution never widens the selection.
4. Validate all hard dependency edges and detect cycles with a readable cycle path.
5. Topologically order dependencies before dependents, breaking independent ties by plugin ID.
6. Optional dependencies never auto-enable plugins. When enabled and compatible, add ordering edges only if acyclic; reject an optional-edge cycle with a diagnostic. An enabled incompatible optional dependency is unavailable to that consumer.
7. Verify required host features are available before importing affected plugins. Import and prepare only resolved plugins. Record dependency failure transitively; independent plugins continue unless an application-required plugin fails.

Omitting `enabled_plugins` or setting it to `None` selects all plugins discovered from approved sources by default. An explicit collection selects only those IDs, and `enabled_plugins=()` selects none. Apply persisted per-plugin toggles within that selection. A new eligible plugin defaults to enabled, even if the app has saved preferences for other plugins. Safe mode and application source policy take precedence. Selection determines desired enablement. Imports still depend on metadata validation and dependency resolution.

The management UI can propose enabling eligible dependencies or disabling reverse dependents, show the resulting changes and save the toggles together for restart. It cannot widen an explicit allowlist.

### Lifecycle

Private states: `discovered`, `disabled`, `blocked`, `preparing`, `starting`, `ready`, `failed`, `stopping`, `stopped`. Persist desired enablement separately from actual state.

Every plugin inherits the public `mkapp.sdk.Plugin` base class, including first-party extension plugins. The entry-point factory `create_plugin() -> Plugin` returns an instance; the loader checks its type before preparation. The base class supplies synchronous `prepare(ctx)` and asynchronous `start(ctx)`/`stop(ctx)` hooks with no-op defaults. Plugins override only the hooks they need; headless plugins have no UI requirements. No abstract hooks, required superclass calls, automatic registration decorators or subclass discovery are needed.

`Plugin` defines lifecycle only. Services, grants and resource ownership belong to the host-created `PluginContext`; inheritance does not grant capabilities. The base class has no constructor side effects, hidden background work or implicit context injection. A plugin may retain its supplied context for later lifecycle work, but the host owns its lifetime. Actions receive their own invocation-scoped `ActionContext`; windows receive a `WindowContext`. Small protocols remain appropriate for service/provider interfaces; they do not replace these entity inheritance contracts.

Construction/import must be side-effect-light. `prepare` registers action/window classes, command specs, component factories and other contributions; it performs no blocking work or ImGui calls. All successful preparation completes before start dispatch. `start` runs in dependency order, performs bounded initialization and can call already-ready dependencies. New work is admitted only after owner readiness. Roll back a failed scope and block dependents. `stop` runs once for every plugin whose startup began, including partial failures, with a deadline and cancellation support. Host resource cleanup always runs independently of whether a plugin overrides `stop` or that hook succeeds.

Factories, preparation and lifecycle run on the runtime thread. Actual render callbacks and GUI resource hooks run on the GUI thread through a bounded host bridge. GUI registration makes a factory available; it does not instantiate graphics resources on the runtime thread.

No live unload, import reload or hot dependency changes in v0.1. A runtime failure revokes admission to the affected plugin and cleans up its scope; Python modules remain imported. Never claim arbitrary Python can be safely unloaded.

At shutdown, reject new ordinary work, quiesce dependent plugins in reverse dependency order, cancel workers, close windows on the GUI thread, finish bounded lifecycle calls and then close sockets in their owning threads. Keep the bus usable for dependency cleanup until plugins stop; reject calls to already stopped owners. Terminate the shared ZeroMQ context only after socket owners have joined. Default `LINGER=0` after the graceful drain deadline. GUI and runtime shutdown coordinate through the bridge so neither waits on a thread that is already blocked joining it.

## 6. Execution and frame ownership

| Execution domain | Owns | Must never do |
| --- | --- | --- |
| Main/GUI thread | Native window, graphics context, ImGui, render callbacks, GUI-local view models | Block waiting for bus replies, workers, file/network I/O or runtime thread |
| Runtime asyncio thread | Broker socket, plugin client sockets, lifecycle, async handlers, state service, routing | Call ImGui or run long synchronous callbacks |
| Named thread worker | Explicit blocking I/O jobs and a cooperative stop token | Touch GUI, share ZeroMQ sockets or mutate another owner's state |
| Named process worker | Explicit CPU jobs in spawned child processes | Inherit live graphics contexts, sensitive handles or parent sockets |

`WindowContext` provides `submit(action, payload, on_success=..., on_error=...) -> RequestHandle`, which returns immediately. It places a bounded request into a thread-safe ingress queue. The runtime sends it over the originating plugin's DEALER connection. Completion produces a typed result for the GUI inbox. Callbacks and view-model updates are applied on the GUI thread at frame boundaries; rendering reads a stable snapshot. GUI code never calls `future.result()` to wait for unfinished work. Callbacks are scoped to the window instance and discarded after closure.

The three host-created contexts expose domain-appropriate capabilities: `PluginContext` supports registration and lifecycle services; `WindowContext` provides stable instance identity, validated opening arguments, GUI-scoped components and non-blocking submission; `ActionContext` provides the owner-scoped runtime services, checked caller identity, deadline and cancellation signal for one request. A context is not a freely constructible grant or serializable object. An action must not retain an invocation context after its handler returns. A window may retain its window context until closure. These restrictions preserve ownership without making base classes service containers.

Runtime-to-GUI operations are immutable directives in a bounded queue. The GUI drains directives before rendering, with both count and elapsed-time budgets. Worker results are transferred as owned immutable values/copies; no shared mutable dictionaries. Coalesce replaceable progress updates, never commands or final results. Include owner scope generation to discard late replies after cleanup.

Each render callback is synchronous. Detect slow callbacks with metadata-only timing, isolate Python exceptions to the owning window, and allow a failed window to be closed. Do not assume exception catching can recover corrupted ImGui stack balance or native crashes. Offer scoped Begin/End helpers and require balanced stacks.

## 7. Action bus and MessagePack protocol

### Topology

One ROUTER broker binds a random per-app-run `inproc://` endpoint. Each plugin has a DEALER client; built-in services are scoped clients too. Broker and client socket I/O are owned by the runtime thread, using `zmq.asyncio`; they share one context. GUI/worker submission uses thread-safe queues, not cross-thread socket calls. Internal host lifecycle bookkeeping may use direct calls; plugin-to-plugin application communication always uses SDK actions.

This single topology carries targeted commands, queries, replies and explicitly subscribed events. Avoid a second PUB/SUB transport and its startup subscription race in v0.1. SDK subscriptions become broker routing records, with acknowledgement before publication begins. No network listeners, external brokers or persistent message log initially.

### Envelope

One bounded MessagePack map per routed message, with fields appropriate to its kind:

```json
{
  "protocol": 1,
  "kind": "request",
  "id": "opaque-request-id",
  "source": "example.notes",
  "action": "example.documents.open.v1",
  "timeout_ms": 5000,
  "payload": {"document_id": "public-document-id"}
}
```

Kinds are `request`, `reply`, `event`, `subscribe`, `unsubscribe`, and `cancel`. Replies carry `correlation_id` and exactly one of a validated result or structured error. Events carry a namespaced action and validated payload. Source is assigned/checked against the host-owned connection identity, never trusted from submitted bytes. The example ID must actually be non-sensitive under application policy; a name or document identifier can itself be sensitive.

Action names include a contract major version. One provider owns each request action; zero or more consumers subscribe to an event. Exact event subscriptions initially; defer wildcard routing. Action registration declares kind, payload/result validators, concurrency, deadline and whether the operation is idempotent. No inference of authorization from a string prefix alone. SDK checks declared action grants at routing time, acknowledging that in-process code can bypass SDK controls.

### Action objects

An executable request operation subclasses `Action`, declares `id`, `input` and `output` validators, and implements `async handle(payload, ctx: ActionContext)`. Optional class metadata supplies `timeout_ms`, `max_concurrency` and `idempotent`; host security and resource ceilings still apply. Construction takes no arguments and performs no I/O or registration. Missing required metadata or an unimplemented handler is rejected at registration.

#### Annotated action functions

`@contract` is the SDK shorthand for `@dataclass(frozen=True)`, with the same dataclass behavior and shallow immutability. Either spelling can define an action input or output. A contract class has no registration or transport side effects.

`@action("example.market_data.series.v1")` turns an async function into an `Action` subclass with the standard execution defaults. Its only decorator argument is the versioned action ID. The function takes two annotated parameters in order, `(request: Input, ctx: ActionContext)`, and annotates the return as `Output`. Both `Input` and `Output` are frozen dataclass types. The decorated name refers to the generated action class, so pass it to `ctx.actions.register` in `prepare` and use it anywhere the SDK accepts a local action class reference.

At registration, resolve the function and dataclass annotations with `typing.get_type_hints` and compile the request and response adapters once. Reject invalid signatures, unresolved annotations and unsupported schemas before the action is available. The initial field types are supported wire primitives, typed lists, string-keyed typed maps, nullable forms and nested frozen dataclasses with finite schemas. Require ordinary dataclass fields accepted by the constructor. Missing required fields, unknown fields and invalid values fail validation. If a request omits a field with a dataclass default, use and validate that default. Keep contract construction and any `__post_init__` checks pure and bounded.

Validate the decoded request map before constructing the input dataclass and invoking the function. A `ValueError` from input contract construction becomes a sanitized `invalid_payload` error. Validate the returned output type and fields before encoding its map. Apply the same privacy and wire limits used by handwritten actions. Callers still send and receive wire maps through the existing SDK, and neither Python types nor dataclass objects cross the bus. Dataclass freezing leaves nested containers mutable, so each invocation receives its own decoded values.

#### Shared execution and calling

The host creates one action instance per registration on the runtime thread and reuses it for bounded concurrent invocations. Actions are stateless by convention: request variables and invocation context stay local to `handle`; persistent business state belongs to the owning service. Do not put request payloads, current callers, results or mutable caches on the instance/class. Even `max_concurrency=1` is not permission to retain stale request contexts. Use context-owned workers for blocking I/O/CPU work; an Action class is not a worker thread.

Calls accept a string ID, a lightweight shared contract reference, or a locally available `Action` subclass as a convenience. The SDK normalizes each to the registered ID; it never invokes the class directly, registers it implicitly, imports a remote implementation or sends a class/instance/context over the bus. Class references confer no permission. Cross-plugin consumers use IDs or contract-only packages rather than importing the provider implementation. Validators and protocol records remain ordinary typed data/functions. Events remain validated message contracts with subscriptions, not executable Action instances; no forced event inheritance hierarchy.

### Event handlers and base events

`@on("example.catalog.changed.v1")` attaches one exact event ID to an async handler without changing the callable. A plugin opts in during `prepare` with `ctx.events.subscribe(self.on_catalog_changed)`. This uses the same subscription path as `ctx.events.subscribe(event, callback)`. The decorator performs no registration, I/O or scanning at import time. The SDK validates the handler signature and event ID at registration, and the host checks the event contract and subscription permission. Resolve and acknowledge subscriptions from preparation before their owner becomes ready. Handlers receive a validated payload and run as bounded runtime tasks under their plugin's resource scope. They are eligible for delivery only while the owner is ready and the subscription is active. Failure or teardown revokes subscriptions and cancels outstanding delivery within the owner budget.

`BaseEvents` is a public namespace of constants for host-published application events. The host alone can publish these IDs, with an empty map as the v1 payload. They are independent of optional public plugin registry announcements.

| SDK constant | Wire event ID | When the host publishes it |
| --- | --- | --- |
| `BaseEvents.START` | `mkapp.app.start.v1` | Once when the initial plugin startup attempts have settled and the application enters its running state. |
| `BaseEvents.SHUTDOWN` | `mkapp.app.shutdown.v1` | Once when graceful shutdown begins, before reverse-order plugin stop. The host gives ready subscribers a bounded dispatch opportunity. |
| `BaseEvents.KILL` | `mkapp.app.kill.v1` | Once if the host enters forced shutdown or escalates to it, while the event bus is still available. The host attempts delivery without delaying escalation. |

Base events use the existing best-effort event route. Late subscriptions receive no replay. Abrupt OS termination or a crash can prevent delivery, including `BaseEvents.KILL`. Startup dependencies and mandatory cleanup use `Plugin.start`, `Plugin.stop` and host-owned resource scopes.

Message values are `None`, booleans, bounded integers, finite floats, strings, bytes, lists and maps with string keys. Reject custom objects, unknown fields where contracts require it, arbitrary extension types, non-finite numbers, unsupported protocol versions and recursive/deep structures. Use `packb(use_bin_type=True)` and `unpackb(raw=False, strict_map_key=True)` with supported explicit limits. Check raw frame length before unpacking and decoded depth/item counts before dispatch. Select a maintained MessagePack version and verify its real limit arguments in implementation; never use pickle or `send_pyobj`/`recv_pyobj` for action messages.

Initial configurable limits: 1 MiB encoded frame, nesting depth 16, 10,000 aggregate decoded items, 256 admitted requests per plugin, 1,024 queued events per plugin, 5-second default request timeout and 2 ms/128-directive GUI drain budget. These are starting budgets to validate, not latency promises. Bound ZeroMQ queues via HWM/MAXMSGSIZE where supported and enforce application admission limits as well. Reserve bounded control capacity for shutdown/cancel/reply traffic.

### Delivery and failure semantics

- No durable delivery, replay, exactly-once claim or automatic retry.
- Route an admitted request once in a running session. Ambiguous timeout/crash can leave a side effect committed; return that uncertainty rather than silently retrying.
- Use monotonic deadlines within this process. Timeout covers queueing and execution; clamp caller timeouts to application maxima. Cancellation is best effort, never proof that a side effect did not happen.
- FIFO admission per sender connection; no global order or handler completion-order guarantee. Serialize state mutations in the state owner.
- Reject a full request queue with `busy`; do not block GUI or broker. Never drop an accepted final reply silently; if its requester disappears, discard with metadata-only accounting.
- Events are best effort, bounded, session-local. Optional progress events may be coalesced; report overflow. State/registry clients recover from authoritative snapshots and revisions.
- Await handlers as tasks with per-owner concurrency limits. The broker must not await one handler inline. Detect same-action reentrancy and bound nested call depth; timeouts remain the fallback for cross-plugin call cycles.

Standard error codes: `invalid_message`, `invalid_payload`, `permission_denied`, `feature_unavailable`, `unknown_action`, `not_ready`, `busy`, `timeout`, `cancelled`, `owner_failed`, `conflict`, `internal_error`. Public errors contain safe codes and fixed/sanitized text; no payloads, secret exception strings or tracebacks by default.

## 8. Workers

`ctx.workers.register(name, entry, kind, max_concurrency)` declares a namespaced worker. `ctx.workers.submit(name, input)` returns a non-blocking handle; repeated registration of a name with different settings is an error. `kind` is `async`, `thread` or `process`; the host chooses a bounded shared executor for the declared kind. Thread workers use cooperative cancellation; process workers use spawn and importable trusted entry functions.

Job inputs/results use the validated non-sensitive wire-value subset. The process-worker adapter transfers MessagePack bytes, not arbitrary plugin instances, contexts or secret objects. Standard process-launch mechanisms may serialize trusted job identifiers internally; they are not exposed as a public object-deserialization bus. Children receive only explicit configuration and must not bootstrap the desktop app on import.

Each job is owned by its plugin resource scope, has a deadline and reports completion/progress through runtime routing. Bounded queues, per-owner quotas and fair scheduling prevent one plugin from exhausting the app. Restart a failed process worker only within a configured budget; never retry the failed job automatically. Thread jobs cannot be forcibly killed safely; require bounded I/O and stop-token checks, and report shutdown timeout honestly. A non-cooperative trusted thread can require whole-process termination. A separate private-operation executor may handle sensitive operations under section 12; ordinary workers reject sensitive inputs.

## 9. Plugin inventory and management

The private host inventory is always present for dependency management. The optional public registry exposes opted-in plugin IDs, versions, capabilities and ready/stopped status, not secrets or arbitrary plugin objects. Provide a revisioned snapshot and events `mkapp.registry.ready.v1`, `stopping.v1`, `stopped.v1`, `failed.v1`. On startup ready follows successful `start`; stopped is a logical lifecycle event during shutdown/cleanup, not a claim of Python module unload. Private plugins can still expose known action contracts without public announcements.

Consumers subscribe with acknowledgement, then request a snapshot; reconcile revisions and repeat after overflow. Applications can suppress announcements entirely. Required dependencies use the host graph, so announcement opt-out cannot break startup resolution.

The optional management window displays discovered, desired, actual and blocked states; version/source; public capabilities; dependency diagnostics; pending restart changes; and safe error codes. Toggle buttons edit desired configuration atomically. They never start/stop plugins immediately. The restart button flushes configuration, requests orderly shutdown and delegates relaunch to an application-provided launcher using an argument array. If no launcher exists, show a restart-required message. If relaunch fails, retain configuration and explain how to restart manually.

The optional third-party onboarding control is disabled unless the application enables it. The visible control offers an approved development directory or local wheel through an installer provider. The provider stages and verifies metadata, wheel paths/tags, hashes and application policy without executing it. Install into an application-managed environment only after trust approval, never the running environment; use standard wheel tooling, explicit offline dependency sources and an atomic environment switch at restart. Do not execute sdists/build hooks or contact package indexes by default. Package installation is arbitrary-code onboarding, not a sandbox. A v0.1 host may expose the provider protocol and use externally installed wheels before shipping a managed installer.

## 10. Command palette and independent autocomplete

The optional palette opens on Cmd+P on macOS and Ctrl+P elsewhere, with remappable keys. Its default location is an app-owned strip at the bottom of the main window; opening reserves space above it. It is independent of docking. SDK options include height, overlay/reserved behavior, initial visibility, shortcut scope and placement. Respect modal dialogs, keyboard capture, IME composition and application shortcut priority. Opening the palette focuses its input; Escape closes it or dismisses completions first.

`CommandSpec` declares stable ID, a multiword command path, description, typed fields, action mapping and optional visibility predicate. Commands dispatch the same validated actions as other UI. Never implement a second command-only execution path. Plugin code supplies any sensitive input through a private prompt; the palette only triggers that prompt.

Proposed v0.1 grammar:

```ebnf
invocation = command_path, { space, field } ;
field = identifier, "=", value ;
value = quoted_string | number | "true" | "false" | "null" | enum_token ;
```

Example: `workspace open name="Research" read_only=true`. Resolve the longest registered command path before parsing fields. Support escaped quotes/backslashes, whitespace, Unicode strings and cursor spans. Reject duplicate/unknown fields, invalid types, missing required values and trailing garbage with exact field spans. Typed defaults come from `CommandSpec`. v0.1 supports structured invocations, not arithmetic, arbitrary attribute access, pipelines or Python evaluation. A parser extension may add a bounded expression AST with explicit operators later; never use `eval` or `exec` on palette input.

Autocomplete is a separately enabled extension, not a palette dependency. It consumes the palette's parse state, cursor span and command catalog to suggest command/subcommand paths, field names, enum/boolean values and registered argument candidates. Providers return insertion text, replacement span, label and optional safe detail. Deduplicate and rank deterministically; debounce and cancel stale asynchronous queries; cap results and provider execution time. Tab accepts the selected candidate, arrows move selection and Enter submits only a valid complete command. Without autocomplete, parsing, execution and field validation still work.

Do not persist raw palette input or command history by default. No secret values in fields, completion suggestions, previews or error strings. A `secret` field type opens a direct private input flow rather than accepting inline sensitive text.

## 11. Windows, tiles and reusable components

Every plugin window subclasses `Window`, declares a stable factory `id` and safe display `title`, and implements synchronous `render(ctx: WindowContext)`. `ctx.windows.register(ComputeWindow, open_on_start=True)` registers the class; it does not construct it. The host creates a new no-argument instance on the GUI thread for each open request, then calls optional no-op `on_open(ctx)` and ultimately `on_close(ctx)` hooks there. Validated immutable opening arguments are available as `ctx.arguments`, not injected into an arbitrary constructor. `open_on_start` is queued only after plugin readiness.

Each instance owns its UI state; never share mutable view state through class attributes or the plugin object across execution threads. Multiple instances have stable opaque instance IDs independent of class/factory IDs and display titles. The host owns outer Begin/End framing, ID scopes, focus, callback delivery and cleanup even when a hook raises. The window owns balanced scopes for widgets it opens inside its render method. Closing revokes its context, invalidates completions and disposes window-scoped resources; repeated cleanup is idempotent. Constructors/hooks must not block; background work uses `ctx.submit`. No required superclass calls. SDK registration, rendering helpers and window-local state exist without tiles; disabled tiles use normal floating windows.

With tiles enabled, `mkapp.windows.open.v1` accepts a registered factory ID, safe initialization arguments and optional placement `{anchor_window, side, ratio}`. Sides are `left`, `right`, `above`, `below`, `tab`. A button uses its current `WindowContext.instance_id` as anchor to request a new window below its containing tile. Do not ship raw ImGui pointers or dock-node IDs over the bus.

The UI adapter resolves the stable anchor to its current dock leaf and applies the operation before the next frame. The GUI rechecks anchor lifetime at execution. Clamp split ratios to configured limits, preserve minimum sizes and reject invalid requests. A missing/stale anchor defaults to a documented root placement with a result indicating fallback; applications may configure strict rejection instead. A floating anchor is first placed in the root dockspace or rejected under strict policy. Adapter code owns version-sensitive DockBuilder calls. Use HelloImGui's docking facilities wherever they cover the operation; validate dynamic relative splits in an early spike.

Optional layout persistence contains only factory IDs, opaque window IDs, geometry and safe layout settings. Do not persist document titles, user content, secret identifiers or arbitrary window state in ImGui INI files. Disable automatic INI persistence until the adapter can enforce this rule. Missing plugin windows are skipped with a safe diagnostic.

Reusable components are versioned namespaced render factories registered through `ctx.components`, e.g. `example.widgets.table.v1`. Other plugins declare the provider dependency and resolve factories through the SDK. Component invocation is a local rendering call, not inter-plugin business messaging. Components accept immutable view data and emit intents through caller-owned callbacks/SDK actions, own no undisclosed threads or global state, and scope ImGui IDs to caller and instance. Providers document style, focus and resource ownership. This permits raw ImGui composition without inventing a declarative UI language.

## 12. Sensitive data and privacy

### Threat model

Protect against accidental propagation through bus serialization, logs, global state, cache, local storage, config files, layout, history, diagnostics, crash reports and improperly persisted credentials. Enforce least privilege in SDK usage and fail closed when a suitable OS credential store is unavailable. Trust the OS, app process and all imported plugins; an SDK capability is not an OS security boundary.

Mandatory defaults: no telemetry, remote crash upload, payload logging, secret history or plaintext storage fallback. App privacy policy cannot be weakened by a plugin. Optional diagnostic events contain allowlisted operational metadata only. Treat plugin IDs, action names, filenames and identifiers as potentially sensitive too; applications may suppress diagnostic identifiers and use ephemeral labels.

### Two different data classes

Small credentials use the OS credential store via a validated backend. Large sensitive documents and records use private in-memory processing or an application-provided encrypted document backend; a keyring is not a general document database. Persistent encryption uses maintained libraries and OS-protected keys; no custom cryptography. If no approved encrypted backend is configured, persistent sensitive-document operations fail; they never fall back to global state or plain files.

### Direct private API

`ctx.private` supplies scoped credential access, private operation invocation and explicit grants. It is not an action endpoint. The application creates unforgeable opaque handles stored in a host table, tied to the owner, grantee, permitted operation, expiry and lifecycle generation. Handles are not strings/integers and cannot be serialized by the SDK MessagePack codec. They carry redacted representations and cannot enter ordinary worker inputs, action payloads, config or state.

Plugin A can invoke an approved private operation offered by plugin B through a direct scoped SDK call, passing a restricted handle without putting it on the bus. The service checks the caller, grantee and operation, then runs the operation in a bounded private executor. Return results remain private handles or explicitly declared non-sensitive results; no automatic declassification. This is the intentional exception to bus-only plugin communication, alongside local UI components and host lifecycle hooks.

Prefer operations that use a credential internally over returning raw credential strings. `SecretValue`/`SensitiveValue` wrappers support narrow explicit access when unavoidable, redact `repr`, and are rejected by serialization and logging adapters. Wrappers cannot detect a plugin copying sensitive bytes into an ordinary string. Code review and application-defined schemas remain necessary; arbitrary secret scanning is not a security proof.

GUI private input stays in a window-local private buffer and calls the direct service; it never becomes a palette field value or shared state entry. Avoid clipboard actions by default. Python and ImGui may retain immutable string copies; do not promise reliable memory zeroization. Clear accessible references promptly, revoke handles on scope close/lock, and define application behavior on session lock and credential-store locking. Do not persist sensitive window titles or field values. A stronger requirement for protected memory requires a separate audited native design.

Security backend extensions must be explicitly selected by the application at bootstrap, meet the same fail-closed contract and have contract tests. Reject plaintext/null/unavailable keyring backends and audit platform-specific access behavior, including access shared by programs using the same Python executable. Secure-storage failure need not kill an empty shell, but all dependent sensitive operations fail with safe codes.

## 13. Global state

The optional global state service stores non-sensitive MessagePack-compatible values. Access is exclusively through `mkapp.state.get.v1`, `set.v1`, `delete.v1`, `snapshot.v1` and `changed.v1` actions. An SDK convenience facade still uses these actions. Keys are namespaced and registered with schemas, size limits and read/write grants; external writes require explicit owner permission. Missing keys are distinct from a stored null.

The runtime service is the single writer. Each key has a monotonically increasing revision; `set`/`delete` optionally require `expected_revision` for compare-and-set. Return `conflict` with safe revision metadata on mismatch. Notifications carry key and revision, not values by default. Subscribe first, obtain a snapshot and reconcile revisions; refresh after overflow. Define deletion revisions/tombstones for the active session so recreated keys cannot reuse a stale revision.

Persistence defaults off. An optional provider stores only keys explicitly marked persistable, atomically, under per-app restrictive permissions. Its default is a small versioned file. Validate/migrate stored schemas and quarantine corrupt state without printing content. On failed persistent mutation do not claim durable success or emit a committed durable notification. Document whether a key is session-only or durable. Cross-key transactions, event sourcing and distributed consistency are out of scope.

### Cache and local storage

| Service | Use it for | Lifetime and authority |
| --- | --- | --- |
| Global state | Shared settings and other coordination that needs revisions and subscriptions | Authoritative for registered state keys, with optional persistence for selected keys |
| Cache | Recomputable results, such as a derived view of a public catalog | Session-local and disposable. Expiry and eviction produce a miss |
| Local storage | Plugin-owned records, such as named workspace presets | Durable across restarts after successful commit |

Both optional extensions are headless and expose versioned `Action` subclasses through the existing bus. Plugins register namespace specs during `prepare`; specs declare bounded key/value schemas and a stable namespace ID. The host binds the namespace to its registering plugin, enforces ownership from the authenticated bus source, and rejects duplicate registrations. Access is owner-only in v0.1. Consumers of another plugin's data call that plugin's public actions. SDK helpers use the same routes and validation. Both extensions accept only explicitly non-sensitive MessagePack-compatible data and reject private handles before serialization. Cache eviction and storage failure cannot select another persistence backend.

**Cache.** `mkapp.cache.get.v1`, `put.v1` and `delete.v1` use a built-in runtime-owned in-memory provider. A lookup returns a hit flag, which distinguishes a stored null from a miss. Each namespace has a bounded TTL measured from put with a monotonic clock. Reads may update recency but never extend TTL. Enforce per-namespace and app-wide entry and encoded-byte budgets, removing expired entries and evicting least recently used entries when needed. Reject entries that cannot fit even in an empty cache. Clear entries on owner teardown, application lock or restart. A plugin handles misses and invalidation explicitly, for example by using an input revision in the key. Automatic refresh, write-through and durable caching are deferred.

**Local storage.** `mkapp.local_storage.get.v1`, `put.v1` and `delete.v1` address a single record by namespace and key. Distinguish a missing record from a stored null. A put replaces that record atomically, and delete is idempotent. The built-in provider uses one app-local DuckDB file, a versioned record table keyed by namespace and key, and bounded MessagePack values tagged with their schema versions. The provider uses fixed parameterized statements. The SDK exposes typed record operations, without arbitrary SQL, paths, native handles or public multi-record transactions. Global state keeps its own persistence provider and has no implicit link to this database.

One host-managed storage thread opens, uses and closes the DuckDB connection. All database I/O and migrations run on that thread with bounded admission, off the GUI and broker threads. One app process owns read/write access to the file. Stop admitting work at service shutdown, cancel queued jobs, give in-flight work a bounded drain, and close on the owner thread. Report a shutdown deadline if a native call outlives it. A write succeeds only after commit. Roll back failed transactions when possible. If a deadline or connection failure leaves the commit outcome unknown, reconcile through a successful read after the outstanding write has settled and the provider is usable.

The application chooses a path in its restricted local data directory. Apply the same permissions to database, WAL and temporary files. Disable DuckDB external access and automatic extension installation/loading and verify those settings with the selected release. Bound queued operations, record count, logical bytes and engine resources, and define a disk-space budget that accounts for WAL and temporary files. Logical record quotas alone do not enforce an on-disk cap. Treat the entire store as ordinary non-sensitive data. The provider migrates its own table format transactionally. A namespace schema change requires an explicit migration that validates transformed values before commit. Missing DuckDB support, an unavailable file, failed migration, corruption or an unsupported schema version disables the affected service or namespace with a sanitized error. Preserve the data for recovery without silently resetting it or falling back to memory. Revoke access on plugin teardown while preserving committed records.

## 14. Public SDK sketch

Target surface; current executable signatures and examples are in [EXAMPLES.md](EXAMPLES.md). Implementation status is recorded separately in [IMPLEMENTATION.md](IMPLEMENTATION.md).

The initial SDK adds `Window.visible(ctx) -> bool`, defaulting to true, as a short GUI-thread hook evaluated before the host's outer Begin. This supports transient palette visibility and shortcuts without window-owned Begin/End. `Window.presentation` defaults to `"window"`; `"bottom_strip"` selects the current fixed overlay adapter. Configurable strip sizing/reserved-space policy remains target work. Constructors, visibility, open/close hooks, rendering and completions all remain GUI-thread-only.

See [EXAMPLES.md](EXAMPLES.md) for current runnable applications and SDK usage, and [docs/DESIGN_EXAMPLES.md](docs/DESIGN_EXAMPLES.md) for the original broader design sketches. The latter describe target APIs and do not establish runtime support. [IMPLEMENTATION.md](IMPLEMENTATION.md) distinguishes executed evidence from remaining requirements.

```python
from mkapp.sdk import (
    Action, ActionContext, Plugin, PluginContext, Window, WindowContext, imgui_bundle,
)

imgui = imgui_bundle.imgui


class RefreshAction(Action):
    id = "example.notes.refresh.v1"
    input = RefreshInput
    output = RefreshOutput
    timeout_ms = 5000
    max_concurrency = 1

    async def handle(self, payload, ctx: ActionContext):
        return await ctx.workers.submit("example.notes.scan", payload)


class NotesWindow(Window):
    id = "example.notes.main.v1"
    title = "Notes"

    def __init__(self) -> None:
        self.status = "Ready"

    def render(self, ctx: WindowContext) -> None:
        imgui.text(self.status)
        if imgui.button("Refresh"):
            ctx.submit(RefreshAction, {},
                       on_success=self.updated, on_error=self.failed)

    def updated(self, result) -> None:
        self.status = "Refreshed"

    def failed(self, error) -> None:
        self.status = f"Refresh failed: {error.code}"


class NotesPlugin(Plugin):
    def prepare(self, ctx: PluginContext) -> None:
        ctx.workers.register("example.notes.scan", "example_notes.jobs:scan",
                             kind="thread", max_concurrency=1)
        ctx.actions.register(RefreshAction)
        ctx.windows.register(NotesWindow, open_on_start=True)
        if ctx.features.has("palette"):
            ctx.commands.register(notes_refresh_command())


def create_plugin() -> Plugin:
    return NotesPlugin()
```

`RefreshInput`, `RefreshOutput` and the command factory are plugin-owned validators/specs, not a mandatory validation library. Command targets and `ctx.submit(RefreshAction, ...)` normalize to the same action ID and bus path. Rendering and completion callbacks run on the GUI thread; `handle` runs on the runtime thread and awaits managed work. Headless plugins simply omit window registration.

The public base contracts remain small:

```python
class Plugin:
    def prepare(self, ctx: PluginContext) -> None:
        pass

    async def start(self, ctx: PluginContext) -> None:
        pass

    async def stop(self, ctx: PluginContext) -> None:
        pass


class Window:
    # Subclasses supply id and title.
    def on_open(self, ctx: WindowContext) -> None:
        pass

    def render(self, ctx: WindowContext) -> None:
        raise NotImplementedError

    def on_close(self, ctx: WindowContext) -> None:
        pass


class Action:
    # Subclasses supply id, input and output contracts.
    timeout_ms = 5000
    max_concurrency = 1
    idempotent = False

    async def handle(self, payload, ctx: ActionContext):
        raise NotImplementedError
```

Registration rejects Window/Action subclasses that omit their required implementation or metadata; it does not defer that failure to rendering or invocation. Lifecycle hooks remain optional and require no `super()` call. Host code performs ownership and disposal independently. Do not add bus clients, executors or hidden context injection to any of these base classes. Configuration/specs/results stay dataclasses or other simple validated records, while provider boundaries use small protocols. No additional inheritance levels are required.

## 15. Compatibility and validation

Version the SDK, manifest schema, wire protocol and action/component contracts independently. v0.x compatibility is explicit via SDK specifiers; do not promise all 0.x releases are compatible. Unknown action major versions fail clearly. Persisted config, layout, state and local storage records have schema versions and bounded migrations. Refuse unsupported future formats without overwriting them.

Validate the implementation with contract tests for providers, a real ZeroMQ/MessagePack integration path, dependency and lifecycle fixtures, worker shutdown/failure tests, parser/completion tests and OS GUI smoke checks. Use fake credential backends in ordinary tests and platform-backed tests with synthetic credentials only. No real sensitive fixtures or diagnostic snapshots containing content.

Performance targets to measure: idle frame p95 below 16.7 ms on a declared reference machine, no blocking GUI waits, bounded memory under producer overload and bounded cooperative shutdown. Track slow plugin render callbacks separately from host overhead. These are provisional acceptance budgets, not guarantees for arbitrary plugins.

## 16. Deferred scope

Hot loading/unloading, remote bus clients, network plugin marketplaces, untrusted-plugin sandboxing, arbitrary executable palette expressions, custom retained UI trees, distributed state, cross-key transactions, guaranteed event delivery and automatic CPU/I/O inference are excluded from v0.1. Add them only against a concrete use case and an explicit architecture revision.

## 17. Primary references

These references ground library constraints; mkapp-specific contracts above are design choices. Recheck APIs and supported package versions when implementing.

- [Dear ImGui threading FAQ](https://github.com/ocornut/imgui/blob/master/docs/FAQ.md#about-multi-threading): shared-context concurrency constraints.
- [ImGui-Bundle docking demo](https://github.com/pthom/imgui_bundle/blob/main/bindings/imgui_bundle/demos_python/demos_immapp/demo_docking.py): runner and docking integration.
- [PyZMQ thread safety](https://github.com/zeromq/pyzmq/blob/main/docs/source/howto/morethanbindings.md): context sharing and socket ownership.
- [PyZMQ asyncio](https://pyzmq.readthedocs.io/en/latest/api/zmq.asyncio.html): asynchronous socket interface.
- [MessagePack Python security notes](https://github.com/msgpack/msgpack-python#security) and [API](https://msgpack-python.readthedocs.io/en/latest/api.html): decoding controls; verify installed-version parameters.
- [PyPA plugin discovery](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/) and [entry points](https://packaging.python.org/en/latest/specifications/entry-points/): installed-distribution discovery.
- [Keyring security considerations](https://keyring.readthedocs.io/en/latest/#security-considerations): backend and platform limitations.
- [DuckDB Python DB API](https://duckdb.org/docs/current/clients/python/dbapi), [concurrency](https://duckdb.org/docs/stable/connect/concurrency.html) and [configuration](https://duckdb.org/docs/stable/configuration/overview.html) cover persistent connections, file ownership and engine limits.
