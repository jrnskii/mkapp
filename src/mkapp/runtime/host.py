"""Discovery, preparation, dependency-ordered lifecycle and host-owned cleanup."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any

from packaging.version import Version

from mkapp.builtin.actions import ActionsPlugin
from mkapp.builtin.manager import ManagerPlugin
from mkapp.builtin.palette import PalettePlugin
from mkapp.sdk import (
    AppError,
    BaseEvents,
    Features,
    Plugin,
    PluginContext,
    ResourceScope,
)
from mkapp.ui.components import ComponentHost, ComponentService
from mkapp.ui.windows import WindowHost, WindowService

from .bus import ActionService, Bus, EventService, safe_code
from .commands import Catalog, CommandService, CompletionService
from .config import AppConfig
from .discovery import Descriptor, discover, resolve
from .management import Management
from .persistence import StatePersistence
from .privacy import PrivateService, PrivateServiceHost
from .records import Cache, RecordService, State
from .storage import Storage
from .workers import WorkerService


@dataclass
class InventoryEntry:
    descriptor: Descriptor
    desired: bool
    status: str = "discovered"
    error: str | None = None
    plugin: Plugin | None = None
    context: PluginContext | None = None
    scope: ResourceScope | None = None
    started: bool = False


class Host:
    """Construct and use on the runtime thread. GUI adapter has a separate owner."""

    def __init__(self, config: AppConfig, windows: WindowHost) -> None:
        self.config, self.windows = config, windows
        self.bus: Bus | None = None
        self.inventory: dict[str, InventoryEntry] = {}
        self.order: tuple[str, ...] = ()
        self.stopping = False
        self.closed = False
        self.forced = False
        self.private = PrivateServiceHost(config.app_id, config.limits)
        self.record_providers: dict[str, Any] = {}
        self.builtins: dict[str, Plugin] = {}
        self.components = ComponentHost(windows.bridge)
        self.windows.components = self.components
        self.catalog = Catalog() if "palette" in config.extensions else None
        self.management = Management(self)
        self.can_restart = False
        self.restart_requested = False
        self.feature_errors: dict[str, str] = {}
        self.shutdown_errors: list[str] = []

    def load(self, item: Descriptor) -> Plugin:
        # Refuse preloaded/shadowed packages before inserting an approved source.
        if item.package in sys.stdlib_module_names:
            raise AppError("invalid_metadata")
        loaded = sys.modules.get(item.package)
        if loaded is not None:
            origin = getattr(loaded, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(
                item.root / item.package
            ):
                raise AppError("invalid_metadata")
        else:
            spec = importlib.util.find_spec(item.package)
            if spec is not None and (
                spec.origin is None
                or not Path(spec.origin)
                .resolve()
                .is_relative_to(item.root / item.package)
            ):
                raise AppError("invalid_metadata")
        path = str(item.root)
        if path not in sys.path:
            sys.path.insert(0, path)
        module_name, factory_name = item.entry.split(":")
        module = importlib.import_module(module_name)
        result = getattr(module, factory_name)()
        if not isinstance(result, Plugin):
            raise AppError("invalid_factory")
        return result

    async def deadline(self, coroutine: Any) -> bool:
        task = asyncio.create_task(coroutine)
        try:
            done, _ = await asyncio.wait(
                {task}, timeout=self.config.limits.lifecycle_seconds
            )
        except asyncio.CancelledError:
            task.cancel()
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
            raise
        if not done:
            task.cancel()
            # Do not wait indefinitely for trusted code that suppresses cancellation.
            task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
            return False
        await task
        return True

    async def start(self) -> None:
        items = discover(self.config)
        resolution = resolve(items, self.config)
        self.components.dependencies = {
            k: set(d.requires)
            | {
                dep
                for dep, versions in d.optional.items()
                if dep in resolution.desired
                and dep in items
                and items[dep].version in versions
            }
            for k, d in items.items()
        }
        for name in self.config.extensions:
            if name in (
                "tiles",
                "palette",
                "autocomplete",
                "registry_announcements",
                "plugin_manager",
                "state_persistence",
            ):
                continue
            if name == "state":
                provider: Any = State(self.config.limits)
                if "state_persistence" in self.config.extensions:
                    provider.persistence = StatePersistence(
                        self.config.data_dir / "state/state.msgpack", self.config.limits
                    )
                    try:
                        await provider.persistence.load()
                    except Exception:
                        provider.persistence.close()
                        self.feature_errors.update(
                            state="feature_unavailable",
                            state_persistence="feature_unavailable",
                        )
                        continue
            elif name == "cache":
                provider = Cache(self.config.limits)
            else:
                provider = Storage(
                    self.config.limits, self.config.data_dir / "storage/records.duckdb"
                )
                try:
                    await provider.submit("init", {})
                except Exception:
                    await provider.close()
                    self.feature_errors[name] = "feature_unavailable"
                    continue
            self.record_providers[name] = provider
            operations = (
                ("get", "set", "delete", "snapshot")
                if name == "state"
                else ("get", "put", "delete")
            )
            owner = f"mkapp.{name}"
            self.builtins[owner] = ActionsPlugin(
                {f"{owner}.{op}.v1": partial(provider.operate, op) for op in operations}
            )
            if name == "state":
                state_plugin = self.builtins[owner]
                assert isinstance(state_plugin, ActionsPlugin)
                state_plugin.events = {"mkapp.state.changed.v1": lambda v: v}
        if "tiles" in self.config.extensions:
            self.builtins["mkapp.windows"] = ActionsPlugin(
                {"mkapp.windows.open.v1": self.windows.request_open}
            )
        if self.catalog is not None:
            self.builtins["mkapp.palette"] = PalettePlugin(
                self.catalog, "autocomplete" in self.config.extensions
            )
        if "registry_announcements" in self.config.extensions:
            registry = ActionsPlugin(
                {"mkapp.registry.snapshot.v1": self.management.registry}
            )
            registry.events = {
                f"mkapp.registry.{name}.v1": lambda v: v
                for name in ("ready", "stopping", "stopped", "failed")
            }
            self.builtins["mkapp.registry"] = registry
        if "plugin_manager" in self.config.extensions:
            self.builtins["mkapp.manager"] = ManagerPlugin(
                {
                    "mkapp.manager.snapshot.v1": self.management.snapshot,
                    "mkapp.manager.propose.v1": self.management.propose,
                    "mkapp.manager.commit.v1": self.management.commit,
                    "mkapp.manager.restart.v1": self.management.restart,
                }
            )
        for owner in self.builtins:
            items[owner] = Descriptor(
                owner,
                Version("0.1.0"),
                "mkapp:main",
                Path(__file__).parent,
                "mkapp",
                {},
                {},
                (),
                False,
            )
        effective = tuple(
            x for x in self.config.extensions if x not in self.feature_errors
        )
        resolution = resolve(
            {k: d for k, d in items.items() if k not in self.builtins},
            replace(self.config, extensions=effective),
        )
        self.order = (*sorted(self.builtins), *resolution.order)
        self.inventory = {
            k: InventoryEntry(
                v,
                k in resolution.desired or k in self.builtins,
                "blocked"
                if k in resolution.blocked
                else "discovered"
                if k in resolution.desired or k in self.builtins
                else "disabled",
                resolution.blocked.get(k),
            )
            for k, v in items.items()
        }
        self.bus = Bus(self.config)
        self.bus.builtin_owners.update(self.builtins)
        state = self.record_providers.get("state")
        if state is not None:
            state.notify = partial(
                self.bus.publish, "mkapp.state", "mkapp.state.changed.v1"
            )
        for identifier in self.order:
            entry = self.inventory[identifier]
            if any(
                self.inventory[d].status in ("failed", "blocked")
                for d in entry.descriptor.requires
            ):
                entry.status, entry.error = "blocked", "blocked_dependency"
                continue
            scope = entry.scope = ResourceScope()
            self.bus.add_owner(identifier)
            actions = ActionService(self.bus, identifier, scope)
            events = EventService(self.bus, identifier, scope)
            context = entry.context = PluginContext(
                identifier,
                actions,
                WindowService(self.windows, identifier, scope, actions),
                events,
                WorkerService(self.bus.workers, identifier, scope),
                PrivateService(self.private, identifier, scope),
                RecordService(
                    actions, scope, "state", self.record_providers.get("state")
                ),
                RecordService(
                    actions, scope, "cache", self.record_providers.get("cache")
                ),
                RecordService(
                    actions,
                    scope,
                    "local_storage",
                    self.record_providers.get("local_storage"),
                ),
                ComponentService(self.components, identifier, scope),
                CommandService(self.catalog, identifier, scope),
                CompletionService(
                    self.catalog if "autocomplete" in self.config.extensions else None,
                    identifier,
                    scope,
                ),
                Features(frozenset(effective)),
            )
            try:
                entry.status = "preparing"
                entry.plugin = (
                    self.builtins[identifier]
                    if identifier in self.builtins
                    else self.load(entry.descriptor)
                )
                entry.plugin.prepare(context)
            except Exception as error:
                entry.status, entry.error = "failed", safe_code(error)
                await self.cleanup(entry)
        # No start hook runs before all successful preparation has completed.
        for identifier in self.order:
            entry = self.inventory[identifier]
            if entry.status != "preparing":
                continue
            if any(
                self.inventory[d].status != "ready" for d in entry.descriptor.requires
            ):
                entry.status, entry.error = "blocked", "blocked_dependency"
                await self.cleanup(entry)
                continue
            assert entry.context is not None and entry.plugin is not None
            try:
                assert isinstance(entry.context.events, EventService)
                self.bus.starting.add(identifier)
                await entry.context.events.activate()
                entry.status, entry.started = "starting", True
                if not await self.deadline(entry.plugin.start(entry.context)):
                    raise AppError("timeout")
                self.bus.starting.discard(identifier)
                self.bus.ready.add(identifier)
                self.windows.ready(identifier)
                entry.status = "ready"
                await self.management.announce(identifier)
            except Exception as error:
                entry.status, entry.error = "failed", safe_code(error)
                await self.stop_entry(entry)
                await self.management.announce(identifier)
        await self.bus.publish("host", BaseEvents.START, {})

    async def cleanup(self, entry: InventoryEntry) -> None:
        if self.bus:
            await self.bus.remove_owner(entry.descriptor.id)
        if entry.scope:
            entry.scope.close()
        self.private.revoke(entry.descriptor.id)

    async def stop_entry(self, entry: InventoryEntry) -> None:
        try:
            if entry.started and entry.plugin and entry.context:
                entry.started = False
                if not await self.deadline(entry.plugin.stop(entry.context)):
                    entry.error = "timeout"
                    await self.escalate()
        except Exception as error:
            entry.error = safe_code(error)
        finally:
            await self.cleanup(entry)

    async def escalate(self) -> None:
        if self.forced:
            return
        self.forced = True
        if self.bus and not self.bus.closed:
            await self.bus.publish("host", BaseEvents.KILL, {})

    async def stop(self) -> None:
        if self.stopping or self.closed:
            return
        self.stopping = True
        try:
            if self.bus:
                await self.bus.publish("host", BaseEvents.SHUTDOWN, {})
                # Best effort delivery opportunity; hooks/scopes perform required cleanup.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            for identifier in reversed(self.order):
                entry = self.inventory[identifier]
                if entry.status == "ready" or entry.started:
                    entry.status = "stopping"
                    await self.management.announce(identifier)
                    await self.stop_entry(entry)
                    entry.status = "stopped"
                    await self.management.announce(identifier)
                elif entry.scope is not None and entry.scope.active:
                    await self.cleanup(entry)
        finally:
            services = [
                self.record_providers.get("state"),
                self.record_providers.get("local_storage"),
                self.private,
                self.bus,
            ]
            for service in services:
                if service is None:
                    continue
                try:
                    await service.close()
                except Exception as error:
                    self.shutdown_errors.append(safe_code(error))
            if self.private.shutdown_timeout or (
                self.bus and self.bus.workers.shutdown_timeout
            ):
                self.shutdown_errors.append("timeout")
            self.closed = True
