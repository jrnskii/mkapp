"""Desired-state proposals never import, enable or unload running code."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from mkapp.sdk import ActionContext, AppError

from .config import atomic_json
from .records import request

if TYPE_CHECKING:
    from .host import Host


class Management:
    def __init__(self, host: Host) -> None:
        self.host = host
        self.saved = dict(host.config.toggles)
        self.revision = 0

    async def snapshot(self, payload: Any, ctx: ActionContext) -> Any:
        request(payload, set())
        return {
            "plugins": [
                {
                    "id": k,
                    "version": str(e.descriptor.version),
                    "desired": e.desired,
                    "status": e.status,
                    "error": e.error,
                    "pending_restart": e.desired != (e.status == "ready"),
                }
                for k, e in sorted(self.host.inventory.items())
                if not k.startswith("mkapp.")
            ],
            "can_restart": self.host.can_restart,
            "installer_available": False,
        }

    async def propose(self, payload: Any, ctx: ActionContext) -> Any:
        request(payload, {"toggles"})
        toggles = payload["toggles"]
        inventory = self.host.inventory
        allowed = {k for k in inventory if not k.startswith("mkapp.")}
        if self.host.config.enabled_plugins is not None:
            allowed.intersection_update(self.host.config.enabled_plugins)
        if type(toggles) is not dict or any(
            k not in allowed or type(v) is not bool for k, v in toggles.items()
        ):
            raise AppError("permission_denied")
        desired = {k for k in allowed if self.saved.get(k, True)}
        desired.update(k for k, v in toggles.items() if v)
        desired.difference_update(k for k, v in toggles.items() if not v)
        disabled = {k for k, v in toggles.items() if not v}
        changed = True
        while changed:
            before = set(desired)
            for k in sorted(desired):
                for dep, versions in inventory[k].descriptor.requires.items():
                    if dep in disabled:
                        desired.discard(k)
                        disabled.add(k)
                    elif (
                        dep not in allowed
                        or inventory[dep].descriptor.version not in versions
                    ):
                        raise AppError("conflict")
                    else:
                        desired.add(dep)
            changed = before != desired
        return {
            "toggles": {k: k in desired for k in sorted(allowed)},
            "restart_required": True,
        }

    async def commit(self, payload: Any, ctx: ActionContext) -> Any:
        proposal = await self.propose(payload, ctx)
        saved = {**self.saved, **proposal["toggles"]}
        try:
            await asyncio.to_thread(
                atomic_json,
                self.host.config.data_dir / "preferences.json",
                {"schema_version": 1, "toggles": saved},
            )
        except Exception:
            raise AppError("internal_error") from None
        self.saved = saved
        for k, desired in proposal["toggles"].items():
            self.host.inventory[k].desired = desired
        return proposal

    async def restart(self, payload: Any, ctx: ActionContext) -> Any:
        request(payload, set())
        if not self.host.can_restart:
            raise AppError("feature_unavailable")
        self.host.restart_requested = True
        return {"restart_requested": True}

    async def registry(self, payload: Any, ctx: ActionContext) -> Any:
        request(payload, set())
        return {
            "revision": self.revision,
            "plugins": [
                {"id": k, "version": str(e.descriptor.version), "status": e.status}
                for k, e in sorted(self.host.inventory.items())
                if e.descriptor.announce
            ],
        }

    async def announce(self, identifier: str) -> None:
        entry = self.host.inventory[identifier]
        bus = self.host.bus
        if (
            not entry.descriptor.announce
            or "registry_announcements" not in self.host.config.extensions
            or bus is None
        ):
            return
        self.revision += 1
        name = {
            "ready": "ready",
            "failed": "failed",
            "stopping": "stopping",
            "stopped": "stopped",
        }.get(entry.status)
        if name:
            await bus.publish(
                "mkapp.registry",
                f"mkapp.registry.{name}.v1",
                {"id": identifier, "revision": self.revision},
            )
