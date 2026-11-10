# Minimal plugin CLI plan

Status: implemented. This document records the authoring slice and acceptance targets; executed evidence is in [IMPLEMENTATION.md](../IMPLEMENTATION.md). Runtime discovery, SDK registration and startup-only loading retain their current contracts.

## Command surface

Provide one non-interactive command through the existing `mkapp` console entry point:

```text
mkapp plugin create <plugin_id> [-t|--template empty|ui|data] [--app-root PATH] [--app-name NAME] [--dry-run]
```

Examples (for an application named `research`):

```text
mkapp plugin create notes
mkapp plugin create dashboard -t ui
mkapp plugin create prices --template data --app-root examples/market --app-name research
```

- Accept a relative lowercase identifier or dotted suffix and always prefix it with the app name: `example` becomes `myapp.example`. Infer the app name from `[project].name` in the root's `pyproject.toml`, falling back to the root directory name; lowercase inferred names and replace hyphens with underscores. `--app-name` overrides inference. Validate the final ID by the existing descriptor rules.
- Default to `empty`, the smallest starting point. `data` means a headless data-providing plugin.
- Treat `--app-root` as the application directory containing `plugins/`. If omitted, use the current directory as the explicitly chosen authoring root. Do not search parent folders or execute an app script to infer configuration. Explain that applications with another entry-script directory or configured `app_root` must pass that directory explicitly.
- `--dry-run` validates inputs and reports the template, destination and files without writing anything.
- Include concise command help and list the three templates there. Avoid a wizard, separate template-list command or configuration file in the first version.
- Preserve the existing launcher interface and behavior; introduce subcommand dispatch without importing or initializing the GUI for generation, help or argument errors.
- Return exit code `0` on success, `2` for invalid arguments and `1` for generation failures. Report concise errors without tracebacks or file contents. Success reports the created directory and activation instructions.

## Generated layout and naming

For `prices` in an app named `research`, generate:

```text
<app-root>/plugins/research.prices/
    mkapp-plugin.toml
    research_prices/
        __init__.py
```

Derive the Python package name by replacing dots with underscores. Reject an invalid Python identifier, keyword, reserved framework/standard-library name or detected package collision; do not silently rename it. Different plugin IDs that map to the same package must conflict. Validate against existing static descriptors in the destination app's plugin folder without importing their code. Runtime discovery remains authoritative for conflicts with other approved roots or distributions.

Generate the current manifest schema with the supplied ID, initial plugin version `0.1.0`, an SDK compatibility range matching the generator's supported SDK contract, `entry = "research_prices:create_plugin"`, `announce = false` and no dependencies or required optional features. Keep the manifest version and SDK range in one maintained generator definition and verify them against discovery in tests.

Every package imports only the public SDK for framework behavior, defines a direct `Plugin` subclass and exports a no-argument `create_plugin() -> Plugin` factory. Generate only necessary lifecycle overrides. Keep each starter in one module; do not add per-plugin packaging metadata, executors, configuration, persistence or extra dependencies.

## Built-in templates

| Template | Generated behavior | SDK registrations |
| --- | --- | --- |
| `empty` | A valid headless plugin with inherited no-op lifecycle hooks, ready for ordinary Python logic | None |
| `ui` | A floating window displaying a short synthetic greeting; optional local counter demonstrates per-window state | One `Window` subclass registered in `prepare` with `open_on_start=True` |
| `data` | A headless action returning a bounded synthetic numeric sample; accepts a count constrained to `1..128` | One annotated `@action("<plugin-id>.sample.v1")`, with frozen `@contract` input/output records, registered in `prepare` |

The UI window uses `<plugin-id>.main.v1`, a synchronous `render(ctx: WindowContext)`, no-argument construction and the lazy public `imgui_bundle` export. The host owns outer Begin/End. Keep GUI access inside GUI-thread methods; do not add bus waits, disk access or background work to rendering. It must work with tiles, palette and other optional extensions disabled.

The data template follows the runnable synthetic provider example. Its action performs only a small bounded calculation, retains no invocation context and imports no GUI code. Document that real blocking I/O or CPU work needs an explicitly selected managed worker. Consumers call its versioned action ID through the SDK and need application-owned grants; generating a provider does not grant access or generate a consumer.

## Generation safety and activation

1. Parse arguments and validate the template, identifier, derived package and static metadata before writing.
2. Resolve the application root and destination. Reject traversal, escaping symlinks and destinations outside the selected application's `plugins/` directory. Refuse an existing target, including an empty directory or symlink. Provide no overwrite/force mode initially.
3. Render a fixed, small set of packaged templates in memory. Use safe literal/TOML encoding for substitutions, with no evaluation, hooks or downloaded templates. Validate the generated descriptor and Python syntax before publication.
4. Stage files in a temporary directory in the app root, outside the discovery folder. Reserve the destination with exclusive directory creation, move the package into it, then publish the descriptor atomically with a no-clobber hard link on the same filesystem. A concurrent target creation must fail without replacing it. Clean up only known generated files/directories owned by this invocation after failure; never remove an existing plugin or unrelated directory. A forced process termination can leave a reserved directory without a descriptor; it is not discoverable as a plugin and must be inspected/removed manually before retrying. Filesystems without hard-link support fail closed. Do not claim success for a partial plugin.
5. Print the destination and explain how to run or restart the existing application. Default discovery selects eligible plugins; an explicit allowlist, saved toggle, safe mode or source policy may exclude the new plugin. Explain the required manual selection change without editing app code, grants or saved settings automatically.

Generation performs no plugin imports, app startup, credential access, installation, network calls or hot loading. A generated plugin is trusted local Python code under the existing project policy.

## Implementation order

1. **CLI dispatch:** inspect the existing console entry point, preserve launcher compatibility and add the parser using standard-library `argparse`. Keep authoring imports separate from runtime/GUI startup. No new runtime dependency is needed.
2. **Generator:** implement deterministic naming, descriptor rendering, destination validation, dry run and staged no-clobber publication. Keep this as a small internal module with three bundled templates; avoid a template engine or public template-provider API.
3. **SDK starters:** add the empty, UI and data templates against current executable examples and public SDK signatures. Ship template resources in the existing wheel.
4. **Verification and guide:** add focused CLI/generation integration tests, run repository formatting/lint/type checks and relevant discovery/action tests, then document implemented usage in `EXAMPLES.md`. Record actual evidence and native-test limitations in `IMPLEMENTATION.md`.

This authoring slice depends on M1–M3 contracts and can proceed independently of the managed installer and remaining optional feature milestones. It does not establish completion of those milestones.

## Acceptance evidence

- Create each template in a temporary application and discover its descriptor through normal metadata-first discovery. Verify IDs, entry targets, SDK compatibility and factory results. Empty and data plugins start with optional extensions disabled.
- Call the generated data action over the real MessagePack/ZeroMQ route; verify the bounded sample, invalid-count rejection and malformed request rejection.
- Verify generated UI registration headlessly, then run a native smoke check for opening, rendering and closing its window with tiles disabled. Record native rendering as unverified if no GUI environment is available.
- Add and disable a generated plugin through app selection without changing the existing provider or unrelated plugins. Cover omitted, explicit and empty allowlists and restart-only activation.
- Exercise invalid IDs, unknown templates, package-name collisions, existing targets, traversal/symlink escapes, dry run, write failure and concurrent destination creation. Existing files must remain unchanged and no partial discoverable plugin may remain after failure.
- Verify help and generation in a fresh process do not import GUI modules or start runtime services. Confirm existing launcher arguments still work.
- Build/install the mkapp wheel in a clean environment and generate all three templates to prove template resources are included. Reuse the repository's established checks rather than adding a second toolchain.

Defer custom/downloaded templates, interactive prompts, wheel scaffolding, dependency installation, app creation, provider-consumer wiring, upgrades and regeneration until concrete usage requires them.
