# Implementation and acceptance record

Recorded 2026-11-10. This is an initial implementation, not completion of M0–M10 or a sensitive-production release.

## Executed checks

- `uv run --extra duckdb pytest -q`: 43 passing tests, including real inproc ZeroMQ round trips, authenticated source checks, malformed/oversized MessagePack values, pressure, timeout without retry, isolated action contexts, lifecycle failure cleanup, dependency selection, metadata-only discovery, private grants, spawned processes, state CAS, cache eviction, DuckDB reopen/rollback, and actual example composition.
- `uv run mypy`: public and runtime source boundaries pass the configured type check.
- Ruff checks and formatting cover source, tests, runnable examples and experiments.
- `experiments/native_smoke.py`: real native windows, lazy upstream export identity, action results at GUI frame boundaries, dynamic below-anchor docking, stale-anchor fallback, palette and management windows, component reuse, optional services, and cooperative shutdown. All optional extensions were enabled for this check. A prior floating-window check ran with all optional extensions disabled.
- Built host and third-party fixture wheels, installed both in an isolated environment with `uv run --isolated --no-project --with ...`, and ran `experiments/package_smoke.py`. Discovery did not import the entry point. The installed plugin returned an encoded action reply and shut down. Headless execution did not import ImGui-Bundle or DuckDB.
- `experiments/credential_probe.py`: macOS Keychain synthetic write/read/delete and read from a second process using the same Python executable. The shared interpreter can access this credential: this is an observed trust limitation, not isolation.
- `experiments/compatibility.py`: [ImGui-Bundle 1.93.0 release metadata](https://pypi.org/project/imgui-bundle/1.93.0/#files) includes CPython 3.14 wheels for macOS 14+ arm64, manylinux 2.28 aarch64/x86_64, and Windows amd64/arm64. Wheel availability does not establish runtime compatibility.

Tested locally: CPython 3.14.8; macOS 27.0 arm64; ImGui-Bundle 1.93.0; PyZMQ 27.3.0; MessagePack 1.2.4; packaging 26.3; keyring 25.7.0; DuckDB 2.0.0. The lockfile records the exact environment. Dependency ranges are restricted to the API families checked here. Windows, Linux, and other Python versions have not been runtime-tested.

## Implemented behavior

| Area | Current behavior |
| --- | --- |
| Bootstrap/SDK | Headless lazy imports; `Plugin`, `Window`, `Action`; frozen `@contract`; annotated `@action`; host-created contexts; explicit, bounded resource registrations; schema-versioned preferences and atomic restrictive writes |
| Discovery/lifecycle | Immediate app-local children, approved roots and installed distributions; pre-import descriptors; package/ID conflicts; containment; deterministic dependency order; default, explicit and empty selections; saved toggles; safe mode; inherited hooks; independent cleanup after failures |
| Plugin authoring CLI | `mkapp plugin create <plugin_id>` with automatic app-name prefix, `-t`/`--template` for empty/UI/data starters, explicit root/name overrides, dry run, metadata-only conflict checks, exclusive destination reservation and descriptor-last publication; no installation or settings changes |
| Wire/actions/events | One runtime-owned ROUTER and owner DEALERs; bounded primitive codec; validation; per-registration action instances; permission checks; correlation; admission; deadlines; cancel messages; exact subscriptions acknowledged on the wire before readiness; best-effort host lifecycle events |
| GUI | Main-thread construction/hooks/render/completions; immutable opening arguments; separate instances; bounded ingress/completions; stale result rejection; floating windows; optional relative docking and stale-anchor root fallback |
| Privacy | Redacted private wrappers; opaque owner/grantee/operation/expiry grants; revocation; separate bounded private executor; explicit safe-result declassification; native credential backend selection with no plaintext/null fallback; no automatic GUI INI files, telemetry or history |
| Workers | Explicit async, blocking-thread and spawned-process entries; bounded admission retained after caller timeout until actual work finishes; MessagePack byte inputs/results; sanitized failures; bounded shutdown reporting |
| Optional UI | Reusable render components with caller/instance ImGui scopes; command grammar and spans; typed defaults; fixed bottom overlay; Cmd/Ctrl+P and Escape; independently enabled completion suggestions and bounded async action providers |
| Management | Opt-in revisioned registry snapshots/events; independent private management window; dependency-closure proposals inside the allowlist; atomic desired preferences; actual code remains running until restart; application-supplied relaunch callback |
| Records | Single-writer state, CAS and session tombstones, optional atomic persistence, change events; owner cache with monotonic TTL/LRU/entry and byte budgets; independent owner-thread DuckDB records with parameterized transactions, schema refusal and engine resource limits |

Ordinary bus errors contain fixed codes. Private wrappers are rejected by ordinary serialization, workers, state, cache, configuration and local storage. Copying a raw secret into an ordinary string defeats these cooperative protections. Python memory and native widgets do not provide reliable zeroization.

## Plugin CLI acceptance

- `uv run --extra duckdb pytest -q`: 63 passing tests, including 20 new authoring tests for both template flags, automatic app-name prefixing, metadata inference/override, dry run, invalid names, collisions, symlink refusal, failed publication cleanup, concurrent destination preservation, all three generated plugins, real encoded sample-action validation, selection changes, empty allowlists, headless CLI execution and launcher compatibility.
- `uv run mypy`, `uv run ruff check src tests examples experiments` and `uv run ruff format --check src tests examples experiments` passed.
- Built the host wheel into `/tmp/mkapp-cli-wheels` and ran `experiments/plugin_cli_smoke.py` using an isolated installation of that wheel. All three bundled templates generated, loaded and served the synthetic data action successfully.
- `uv run python experiments/plugin_cli_smoke.py --native` opened, rendered and closed the generated floating UI with all optional extensions disabled on the local macOS target; the runtime thread stopped cooperatively.

No Windows/Linux native execution is established by these checks. Publication requires same-filesystem hard links and fails on unsupported filesystems. Forced process termination before descriptor publication can leave an undiscoverable reserved directory requiring manual inspection/removal. App names are inferred from static project metadata or the root directory, with `--app-name` for namespaces that differ from runtime configuration; the generator never executes an application script to extract `AppConfig`.

## Milestone gaps and release gates

Every milestone still needs some of its specified acceptance evidence; none is marked complete solely by the table above.

| Milestone | Remaining material work |
| --- | --- |
| M0 | Windows/Linux native and credential-store execution; real locked/unavailable native-backend behavior on each claimed platform. Ordinary tests simulate backend failure. |
| M1 | Complete bootstrap replacement-provider protocols, explicit provider selection and replacement contract suites. The current host selects its built-in core implementations. Launch override/config migration coverage also needs expansion. |
| M2 | Expand metadata/diagnostic fixtures, including detailed cycle paths, optional-edge combinations and source collision cases. Descriptive/capability/provided-contract descriptor metadata is not implemented. |
| M3 | Broader overload, disappearing-owner, subscription-pressure and forced-shutdown evidence; control/reply capacity stress measurements and diagnostics. Do not infer a latency or loss guarantee beyond tested bounded behavior. |
| M4 | Direct private GUI prompts; application-configured encrypted-document operations and replacement credential-provider wiring; focused independent security review. No encrypted document persistence is currently exposed. |
| M5 | Cooperative worker stop-token API, progress coalescing and process restart-budget policy. Threads currently require bounded jobs/I/O; timeout cannot stop arbitrary Python/native thread code. Non-cooperative threads can outlive host shutdown and require process termination. |
| M6 | Strict-anchor/minimum-size/focus policies, opt-in safe layout persistence, and expanded native failure/disabled-path checks. Current docking uses clamped ratios and root fallback. |
| M7 | Configurable shortcut/placement/reserved-space modes, complete parse-aware insertion coverage inside escaped strings, private prompt fields and manual IME acceptance. The current palette is a fixed 180px overlay; inline secret field schemas are rejected. |
| M8 | Installer-provider protocol/policy wiring and richer restart recovery tests. Managed installation remains unavailable; externally installed approved wheels are supported. |
| M9 | Explicit application schema migration callbacks and future format migrations; disk quota/lock/failure/overflow stress evidence. Version 1 is implemented and unsupported formats are refused without resetting data. Logical DB/engine limits do not impose a hard physical database/WAL disk cap. |
| M10 | Remaining shared provider suites, cross-platform CI/native coverage, reference-machine frame p95 and overload measurements, full leak/security review, and release packaging acceptance. |

The complete target remains [PLAN.md](PLAN.md) and [ARCHITECTURE.md](ARCHITECTURE.md). Unsupported surfaces are not claimed as working functionality. An optional state/storage startup failure disables that service, blocks plugins requiring it before import and leaves unrelated plugins eligible.

## Reproduce wheel installation evidence

```sh
uv build --wheel --out-dir /tmp/mkapp-wheels
uv build --wheel --project examples/wheel --out-dir /tmp/mkapp-wheels
uv run --isolated --no-project \
  --with /tmp/mkapp-wheels/mkapp-0.1.0-py3-none-any.whl \
  --with /tmp/mkapp-wheels/mkapp_example_wheel-0.1.0-py3-none-any.whl \
  python experiments/package_smoke.py
```

Native probes are explicit opt-ins:

```sh
uv run --extra duckdb python experiments/native_smoke.py
uv run python experiments/credential_probe.py
uv run --extra duckdb python experiments/compatibility.py
```
