# mkapp implementation plan

Status: implementation in progress. A runnable framework, optional features and examples now exist. Milestone acceptance is still incomplete; see [IMPLEMENTATION.md](IMPLEMENTATION.md) for executed checks and remaining work. The deliverables below remain the target, rather than a claim that every contract is implemented.

## 1. Outcome

Deliver a minimal Python desktop framework on ImGui-Bundle for building data-science and trading apps as needs arise. A developer can start with a single feature and add data, analysis or UI plugins through `mkapp.sdk` as the workflow grows. Support headless plugins, explicit dependencies, development directories, installed wheels, startup-only loading, asynchronous actions and named workers. Ship optional first-party UI, state, cache and local storage extensions and mandatory fail-closed sensitive-data services.

`ARCHITECTURE.md` owns the runtime contracts. `AGENTS.md` governs implementation work. This plan defines dependency order, deliverables and acceptance evidence. Changes to a contract must update all affected documents and tests together.

`EXAMPLES.md` documents executable packages and current SDK usage. The original design sketches are archived separately. A sketch alone does not establish runtime support or milestone acceptance.

[Minimal plugin CLI plan](docs/PLUGIN_CLI_PLAN.md) records the `mkapp plugin create` authoring slice and its empty, UI and data-provider starters. The generator is implemented; usage is in [EXAMPLES.md](EXAMPLES.md#create-a-plugin), and executed checks are recorded in [IMPLEMENTATION.md](IMPLEMENTATION.md). It is independent of the managed installer.

The current [EXAMPLES.md](EXAMPLES.md) now describes runnable packages and implemented signatures; the original sketches are preserved in [docs/DESIGN_EXAMPLES.md](docs/DESIGN_EXAMPLES.md). The initial Window API includes optional GUI-thread `visible(ctx)` and a presentation hint for transient palette windows. This does not alter the required synchronous `render` contract or the host's ownership of Begin/End.

## 2. Delivery rules

- Use a synthetic market-data provider and an independently added viewer as the first application slice. Keep optional built-ins off for this checkpoint.
- Keep optional built-ins in the same package initially; do not create a monorepo of separately released services.
- Use one registration mechanism for first-party and third-party code.
- Use shallow `Plugin`, `Window` and `Action` base classes with explicit class registration, host-managed instances and domain-specific contexts. Keep configuration, results and command/placement specs as simple records.
- Require real plugin examples that install and run without editing framework source.
- Bound all queues, payloads, workers and lifecycle waits before treating the framework as usable.
- Establish the privacy boundary before adding history, persistence, diagnostics or third-party installation.
- Record API/version decisions from actual library compatibility checks; avoid speculative dependency pins.

### First application checkpoint

At M3, load a one-module headless plugin that serves a bounded synthetic price series through an annotated `@action` function with `@contract` input and output records. Then add a viewer plugin that calls the same action and renders the result in a floating window through the GUI completion bridge. Verify the provider and existing features are unchanged when the viewer is added or disabled. Run the slice with the palette, docking, cache and local storage off. Add worker-backed sampling when M5 lands, using the same public action contract.

## 3. Milestones

### M0 — Validate the foundation

Depends on: nothing.

Deliverables:

1. Choose an initial supported CPython version and validate ImGui-Bundle wheel availability on macOS, Windows and Linux. Prefer Python 3.11+ if the tested dependency matrix permits it. Document exact tested versions and defer unsupported targets explicitly.
2. Run a tiny HelloImGui application on the main thread with one background asyncio runtime thread. Send an update through a bounded thread bridge and render the result without waiting on the background task.
3. Exercise a real inproc ROUTER/DEALER round trip, asynchronous reply correlation, queue pressure and owner-thread socket closure.
4. Validate OS credential-store behavior on each claimed platform, including locked/unavailable backend behavior. Document the trusted in-process plugin threat model.
5. Confirm packaging metadata can locate a wheel's static descriptor without importing its entry point.

Exit evidence: runnable scratch experiments, a short compatibility record and confirmed SDK threading, transport and credential-store constraints. Experiments do not become production abstractions automatically.

### M1 — Bootstrap, SDK and configuration

Depends on: M0.

Deliverables:

1. Add `pyproject.toml`, `src/mkapp`, a console entry point and focused test tooling. Use one package with the dependency direction in the architecture.
2. Define public `Plugin`, `Window` and `Action` base classes, `@contract` as shorthand for a frozen dataclass, immutable specs, service/validation protocols, errors, registration handles, GUI request handles and resource scopes. Define host-created `PluginContext`, `WindowContext` and invocation-scoped `ActionContext`. Plugin lifecycle and Window open/close hooks have no-op defaults. Window render and Action handle are required implementations. No mandatory superclass calls, hidden context injection or deep hierarchies.
3. Implement typed `AppConfig`, precedence, schema-versioned config, atomic writes, per-app paths and immutable security ceilings.
4. Define bootstrap provider contracts and explicit singleton selection. Reject duplicate providers and plugin attempts to relax policy.
5. Start and stop an empty host with all optional extensions off. Add a lazy SDK re-export of the original `imgui_bundle` module. SDK import has no process, GUI, socket or credential side effects.

Exit evidence: empty shell launches/closes; headless SDK imports cleanly; a minimal `Plugin` subclass works with inherited lifecycle defaults; invalid Window/Action classes and duplicate IDs are rejected during registration; registration cleanup is idempotent; invalid config fails before plugin import. Static type checking covers public boundaries. Only Plugin/Window/Action class registration is supported for these entities, without subclass scanning or a second callable/factory API.

Verify a fresh process can import the headless SDK without loading ImGui-Bundle. In a GUI-capable environment, verify that the SDK export is the original module and that a window renders through it.

### M2 — Plugin discovery and lifecycle

Depends on: M1.

Deliverables:

1. Discover immediate children of the app-local `plugins/` folder with static descriptors. Use the app entry script or explicit app root to locate it. Support additional approved directories and approved installed wheel entry points.
2. Validate IDs, version specifiers, SDK/manifest compatibility, required host features, source ownership, import package collisions and approved paths.
3. Implement deterministic dependency resolution, hard-cycle diagnostics, optional dependency rules and transitive blocking.
4. Require entry-point factories to return `Plugin` instances, then implement private lifecycle inventory, `prepare/start/stop`, dependency-ordered startup and reverse cleanup. Host-owned cleanup remains effective when hooks are inherited or overridden.
5. Enable eligible discovered plugins by default. Apply explicit `enabled_plugins` as an allowlist and saved toggles within that selection. Keep desired configuration separate from actual status and provide safe mode that imports no third-party plugins.
6. Add a no-UI plugin and a dependent consumer example with metadata-only import tests.

Exit evidence: disabled/unapproved plugins are never imported; a directory and an installed test wheel follow the same superclass/factory contracts; invalid factory results fail before preparation; duplicate IDs, cycles, missing/disabled dependencies and incompatible versions produce actionable diagnostics. Partial startup failure cleans up only owned resources and leaves independent plugins usable, including when stop is inherited or raises.

Exercise the default folder from a different working directory, a missing folder, an explicit app root, nested folders, and symlink escapes. Verify omitted, explicit and empty allowlists, a new plugin with saved preferences for another plugin, a dependency excluded by the allowlist and safe mode.

### M3 — Action bus and public wire contracts

Depends on: M2.

Deliverables:

1. Implement the versioned envelope, MessagePack codec, strict primitive-value validation, frame/depth/item limits and schema validation.
2. Implement one runtime-thread-owned ROUTER and per-owner DEALER clients over inproc endpoints.
3. Add explicit `Action` subclass registration and validated event contracts, source stamping, permission checks, acknowledged exact subscriptions and correlation IDs. Support `@action(id)` as a generated subclass with validators compiled from the handler's annotated dataclasses during registration. Construct one stateless action instance per registration on the runtime thread and dispatch `handle(payload, ActionContext)` with request-local identity, deadline and cancellation.
4. Add bounded admission, deadlines, best-effort cancellation, standard errors and metadata-only accounting.
5. Dispatch handlers as bounded tasks; isolate handler errors; prevent broker starvation and detect direct reentrancy.
6. Add the GUI ingress/completion bridge and basic floating window host for the first application checkpoint. Construct one registered Window instance per open request on the GUI thread. Supply stable instance IDs and immutable opening arguments through `WindowContext`. Manage outer ImGui scopes, open/close hooks and cleanup, and deliver completions at frame boundaries only while the owning scope is active.
7. Add metadata-only `@on` declarations to explicit subscriptions. Publish the initial `BaseEvents` set on the existing bus from the host lifecycle transitions.

Exit evidence: two separately loaded plugins communicate through real encoded actions; replies arrive without GUI blocking. Local Action-class references, shared contract references and string IDs take the same validated wire route; no classes/instances/contexts are serialized. Concurrent calls keep invocation contexts isolated. Malformed/oversized payloads, unknown versions, forged sources, full queues, cancelled calls, disappearing owners and timeout-after-side-effect cases behave as specified. No automatic retry or silent accepted-reply loss.

Verify decorated and explicit subscriptions share validation and cleanup. Cover startup and graceful shutdown publication order, duplicate shutdown requests, forced escalation with and without a live bus, reserved-ID spoofing, registry disabled, and handler failure or timeout without blocking teardown.

Verify `@contract` has standard frozen dataclass behavior. Test annotated actions with forward references, missing or invalid annotations, unsupported field types, malformed input, invalid output and defaults. Exercise both handwritten and decorated actions over the same MessagePack route.

### M4 — Privacy and sensitive operations

Depends on: M3. Required before persistence or broad third-party onboarding.

Deliverables:

1. Implement validated OS credential backends and reject plaintext, null and unavailable backends for sensitive operations.
2. Add `SecretValue`, private handles, owner/grantee/operation checks, expiry, revocation and direct private operation registration/invocation.
3. Reject private wrappers/handles at every ordinary serialization, state, worker and configuration boundary.
4. Provide an encrypted sensitive-document backend protocol; without a configured approved backend, refuse persistence rather than inventing crypto.
5. Define safe logging/errors, disabled telemetry/history/crash uploads and diagnostic metadata policy. Disable uncontrolled ImGui INI persistence.
6. Add synthetic private-input and credential-use examples that return only safe results.

Exit evidence: synthetic sentinel secrets do not appear in bus captures, logs, ordinary config/state/layout files or exception messages. Cross-owner/expired handles fail; unavailable/locked stores fail closed. Tests explicitly document that copying raw secrets or malicious in-process code defeats cooperative protections. Obtain a focused security review before claiming sensitive-production readiness.

### M5 — Managed workers and responsiveness

Depends on: M3 and M4 for private-operation handling.

Deliverables:

1. Add named async/thread/process worker registration and non-blocking submission.
2. Add bounded executors, per-plugin quotas, explicit workload modes, progress coalescing and lifecycle ownership.
3. Spawn process workers with importable job entries and validated MessagePack byte inputs/results; do not transmit contexts, arbitrary plugin objects or secrets.
4. Implement cooperative thread cancellation, bounded process shutdown, crash reporting and cancellation/late-result behavior.
5. Run private operations through a separately scoped executor; keep them off ordinary worker/bus paths.

Exit evidence: a CPU job uses a child process and a blocking-I/O job uses a thread; UI remains responsive. Worker crashes, full queues, cancellation and plugin shutdown do not leak registrations or deadlock sockets. Non-cooperative thread limitations are visible and documented. Do not claim arbitrary automatic CPU parallelization.

### M6 — Relative tiles and components

Depends on: M3 and M5.

Deliverables:

1. Prove dynamic docking with the selected ImGui-Bundle release. Open a window below an anchor, handle a stale anchor and preserve opaque stable IDs. Resolve any binding gaps before committing the tile API.
2. Add the optional tile provider and `windows.open` action with anchor/side/ratio semantics and stale-anchor policy. Keep floating windows available with tiles disabled.
3. Add balanced inner widget helpers and focus rules for tiles and components.
4. Add versioned reusable render components with scoped IDs and intent callbacks.
5. Persist only safe layout metadata when explicitly enabled. Use opaque native window identifiers.

Exit evidence: a plugin button opens another plugin's registered Window subclass below its own tile through the bus; stale and floating anchors behave consistently. Two instances of one window class have independent mutable UI state. A component is reused in two windows without ID/state collisions. Inherited hooks and failed hooks preserve host cleanup. Disable tiles and verify plugin windows still work. Confirm construction, hooks, rendering and completion callbacks stay on the main thread.

### M7 — Commands, palette and separate completion extension

Depends on: M3, M4, M6.

Deliverables:

1. Define `CommandSpec`, typed field schemas and the bounded structured-invocation parser from the architecture.
2. Add multiword command paths, escaped/Unicode strings, defaults and span-based validation errors.
3. Render the independently enabled bottom palette; implement Cmd+P/Ctrl+P, keyboard focus, Escape, IME-safe editing and configurable placement.
4. Dispatch validated commands through registered actions; sensitive fields launch direct private prompts.
5. Implement autocomplete as an independent extension with command/subcommand/field/value suggestions and provider registration.
6. Add debouncing, result caps, cancellation, stale-query rejection and deterministic insertion spans/ranking.

Exit evidence: `workspace open name="Research" read_only=true` dispatches a typed action; malformed invocations never execute code. Completion works in the middle of input and after subcommands, including escaped strings. Disable autocomplete and confirm the palette still parses/executes. Disable palette and verify no command shortcut is captured. Raw history remains disabled.

### M8 — Opt-in registry and plugin management

Depends on: M2, M3, M6; command contributions use M7 when enabled.

Deliverables:

1. Implement separately enabled public announcements, per-plugin opt-in, revisioned snapshots and readiness/stopping/stopped/failure events.
2. Add the optional management window with actual/desired states, dependency diagnostics and pending restart changes.
3. Add dependency-closure proposals and atomic desired-config commits. Keep toggles and proposed dependencies inside the application's allowlist.
4. Add a host restart request and application-owned relaunch adapter with safe argument arrays and failure recovery.
5. Expose the installer provider protocol and policy-gated directory/local-wheel control. Externally installed wheels remain fully supported without a managed installer.

Exit evidence: a late registry subscriber recovers from snapshot/revisions; opt-out does not affect dependency resolution. Toggles do not change running code. Restart activates the saved desired set. Management and registry can each be disabled independently. Without a configured installer or permissive app policy, third-party import controls are absent or clearly unavailable.

### M9 — Non-sensitive state, cache and local storage

Depends on: M3 and M4. May be developed after M5 independently of UI work.

Deliverables:

1. Add the optional single-writer state service with namespaced schemas and read/write grants.
2. Implement get/set/delete/snapshot actions, per-key revisions, compare-and-set and revisioned change notifications.
3. Add session tombstones and snapshot reconciliation after overflow.
4. Add separately enabled persistence for explicitly persistable keys, atomic writes, migrations, restrictive file permissions and corruption handling.
5. Add the independent in-memory cache with scoped namespaces, monotonic TTL, least-recently-used eviction, entry and byte quotas, and cleanup on owner teardown and lock.
6. Add the independent DuckDB provider behind `mkapp[duckdb]`. Use one connection on a dedicated storage thread. Implement bounded record get/put/delete, transactional writes and schema migrations, and explicit startup and shutdown failure handling.

Exit evidence: competing writes produce a conflict rather than lost updates when using expected revisions; stored null differs from missing; stale revisions fail after delete/recreate. All reads/writes pass through real bus actions. Private values are rejected. Failed durable writes do not report durable success or expose content.

Cache evidence covers expiry without sleeping via a fake monotonic clock, eviction under both entry and byte pressure, cached null, cross-owner denial and cleanup. Storage evidence uses a real DuckDB file for reopen durability, failed transaction rollback, concurrent submission through the single owner, schema migration and refusal of unsupported formats. Cover missing dependencies, file locks, write failures, uncertain timeouts, queue pressure and cooperative shutdown. Verify cache and storage work independently, a clean base install needs no DuckDB import, private values leave no traces in ordinary database or auxiliary files, and database work never runs on the GUI or broker threads.

### M10 — Integration, packaging and release candidate

Depends on: M0–M9.

Deliverables:

1. Ship a minimal app and examples for headless actions, dependency use, workers, private operations, relative tiles, command fields, component reuse, cache and local storage.
2. Build/install third-party fixture wheels in a clean app environment; exercise plugin resource metadata and SDK compatibility.
3. Add provider contract tests, lifecycle failure scenarios, overload checks, GUI smoke coverage and synthetic-secret leakage checks.
4. Validate native shortcuts/docking/process spawn/credential backends on every claimed OS; mark untested targets unsupported.
5. Measure reference-machine idle frame times, queue memory bounds and cooperative shutdown time; record realistic budgets and slow-plugin behavior.
6. Publish SDK reference, plugin author guide, compatibility policy, threat model and troubleshooting/safe-mode instructions.

Exit evidence: an author adds a plugin as a directory or installed wheel using only SDK imports and configuration, without changing framework source. All built-in switches have enabled/disabled coverage; a dependency failure, worker failure, full bus queue and unavailable credential store produce specified safe outcomes.

## 4. Optional follow-up: managed wheel installer

Implement only after startup loading and external wheel installation are stable. It is not required to prove the core framework.

Use standard tooling to stage an approved local wheel plus an explicit offline dependency set into a new app environment. Verify hashes, compatible tags, archive paths, descriptors, dependency constraints and policy. Show provenance and trust implications before approval. Commit an environment pointer for the next restart; preserve the old environment for rollback. Never mutate the running interpreter, install source distributions or execute arbitrary build hooks. Validate failed installs, interrupted switches and rollback before exposing the control in production.

## 5. Acceptance matrix

| Requirement | Evidence |
| --- | --- |
| New features need no core edits | Independent directory and wheel examples import only SDK |
| Folder discovery and default enablement | A valid app-local plugin loads at restart without a configuration change. An explicit allowlist or saved toggle can exclude it before import. |
| Ad hoc feature composition | Add the market viewer through `plugins/` and restart. Disable it through app config and restart again. The provider and unrelated plugins retain their behavior, and a duplicate registration never replaces the original silently. |
| Headless plugins | Action provider runs with no windows/palette/tiles |
| OOP SDK entities | Explicit Plugin/Window/Action subclasses, correct instance lifetimes, context isolation and class-to-ID bus normalization |
| Annotated actions | Name-only decorator, frozen dataclass input/output inference and the same registration and wire format as handwritten actions |
| Required/optional dependencies | Deterministic graph fixtures and lifecycle ordering integration |
| Startup-only loading | Desired-state toggle remains pending until restart |
| Public registry opt-in | Private inventory always works; public metadata omitted when opted out |
| Decorated events and base events | Explicit subscription, validated delivery, host-only publication and bounded shutdown observation |
| Optional built-in windows | Management and palette independently disabled |
| Autocomplete extension | Palette usable with completion disabled; subcommands/fields/values covered |
| Relative tile actions | Button request splits below anchor at next frame boundary |
| Async rendering/workers | GUI never waits; named I/O/CPU jobs run in declared domains |
| Sensitive data bypasses bus | Direct private operation test plus synthetic-sentinel captures |
| Global state via bus | Real encoded get/set/CAS/notification integration |
| Bounded session cache | Real bus hit/miss, expiry, eviction and owner cleanup |
| Optional DuckDB storage | Clean base install, separate opt-in, durable reopen, transaction failure and cross-owner denial |
| Reusable components | One provider used by two consumer windows with scoped IDs |
| SDK extensibility | Contract-tested replacement/contribution examples, selected through config |
| Minimal framework | No duplicated built-in extension mechanism or unnecessary service stack |

## 6. Risks and responses

| Risk | Response |
| --- | --- |
| ImGui API/binding changes | Narrow adapter, relative-split spike before the tile API and a tested dependency matrix |
| Malicious plugin reads process memory | Trusted-plugin policy; do not claim SDK sandboxing |
| Private data copied to plain strings | Explicit private APIs, schema discipline and review; document enforcement limit |
| Queue pressure stalls app | Bounded admission, reserved control capacity and frame budgets |
| Async handler blocks runtime | Explicit worker mode, callback timings and owner limits |
| Thread refuses cancellation | Bounded I/O requirements and visible shutdown limitation |
| Plugin graph cannot resolve | Clear diagnostics and desired-state proposals; never silently import more code |
| Wheel dependency clashes | Dedicated environments and standard packaging tooling |
| Framework grows too broad | One-host vertical slices; defer installer, network and sandbox complexity |
| “Expressions” means a richer language | Keep parser provider seam; confirm concrete use cases before adding syntax |

## 7. Completion criteria

v0.1 is complete when M10 evidence exists, documents match behavior, all required platform checks pass and the security review has no unresolved release-blocking findings. A managed installer may remain a configured external provider; wheel discovery/loading must work. No milestone completion may be inferred from the presence of these plans alone.
