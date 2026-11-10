"""Direct private service. Native stores are selected explicitly, never fallback."""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mkapp.sdk import AppError, PrivateHandle, Registration, ResourceScope, SecretValue

from .codec import Codec
from .config import Limits, check_id


class CredentialBackend(Protocol):
    """Application-approved blocking backend, used only in the private executor."""

    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...


class EncryptedDocuments(Protocol):
    """Application-approved encrypted backend. There is no built-in fallback."""

    def put(self, key: str, value: SecretValue) -> None: ...
    def get(self, key: str) -> SecretValue: ...


def native_backend() -> CredentialBackend:
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        backend = Keyring()
        if backend.priority <= 0:
            raise ValueError
        return backend
    except Exception:
        raise AppError("feature_unavailable") from None


@dataclass
class Grant:
    owner: str
    grantee: str
    operation: str | None
    expires: float
    scope: ResourceScope
    generation: int
    value: SecretValue


@dataclass
class PrivateOperation:
    owner: str
    callback: Callable[[SecretValue], Any]
    safe_result: bool


class PrivateServiceHost:
    def __init__(
        self,
        app_id: str,
        limits: Limits,
        *,
        backend: CredentialBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app_id, self.limits, self.clock = app_id, limits, clock
        self.backend = backend
        self.handles: dict[PrivateHandle, Grant] = {}
        self.operations: dict[str, PrivateOperation] = {}
        self.codec = Codec(limits)
        self.pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="mkapp-private"
        )
        self.futures: set[concurrent.futures.Future[Any]] = set()
        self.closed = False
        self.shutdown_timeout = False

    def prune(self) -> None:
        for handle, grant in list(self.handles.items()):
            if (
                grant.expires <= self.clock()
                or not grant.scope.active
                or grant.scope.generation != grant.generation
            ):
                del self.handles[handle]

    def hold(
        self, owner: str, scope: ResourceScope, value: SecretValue
    ) -> PrivateHandle:
        self.prune()
        if self.closed or not scope.active or not isinstance(value, SecretValue):
            raise AppError("permission_denied")
        if len(self.handles) >= self.limits.pending:
            raise AppError("busy")
        handle = PrivateHandle()
        self.handles[handle] = Grant(
            owner, owner, None, self.clock() + 300, scope, scope.generation, value
        )
        return handle

    def revoke(self, owner: str | None = None) -> None:
        for handle, grant in list(self.handles.items()):
            if owner is None or grant.owner == owner or grant.grantee == owner:
                del self.handles[handle]

    async def execute(self, fn: Callable[[], Any]) -> Any:
        if self.closed:
            raise AppError("owner_failed")
        if len(self.futures) >= 2:
            raise AppError("busy")
        loop = asyncio.get_running_loop()
        future = self.pool.submit(fn)
        self.futures.add(future)
        future.add_done_callback(
            lambda _: (
                loop.call_soon_threadsafe(self.futures.discard, future)
                if not loop.is_closed()
                else None
            )
        )
        try:
            async with asyncio.timeout(self.limits.timeout_ms / 1000):
                return await asyncio.shield(asyncio.wrap_future(future))
        except TimeoutError:
            raise AppError("timeout") from None
        except Exception:
            raise AppError("feature_unavailable") from None

    async def close(self) -> None:
        self.closed = True
        self.revoke()
        self.operations.clear()
        self.pool.shutdown(wait=False, cancel_futures=True)
        pending = [asyncio.wrap_future(f) for f in self.futures if not f.done()]
        if pending:
            _, unfinished = await asyncio.wait(
                pending, timeout=self.limits.lifecycle_seconds
            )
            self.shutdown_timeout = bool(unfinished)


class PrivateService:
    def __init__(
        self, host: PrivateServiceHost, owner: str, scope: ResourceScope
    ) -> None:
        self.host, self.owner, self.scope = host, owner, scope

    def hold(self, value: SecretValue) -> PrivateHandle:
        return self.host.hold(self.owner, self.scope, value)

    def grant(
        self, handle: PrivateHandle, grantee: str, operation: str, *, ttl: float = 30
    ) -> PrivateHandle:
        self.host.prune()
        original = self.host.handles.get(handle)
        check_id(grantee)
        check_id(operation, versioned=True)
        if (
            original is None
            or original.owner != self.owner
            or original.grantee != self.owner
            or original.operation is not None
            or not 0 < ttl <= 300
        ):
            raise AppError("permission_denied")
        granted = self.hold(original.value)
        self.host.handles[granted] = Grant(
            self.owner,
            grantee,
            operation,
            min(original.expires, self.host.clock() + ttl),
            self.scope,
            self.scope.generation,
            original.value,
        )
        return granted

    def register(
        self,
        identifier: str,
        callback: Callable[[SecretValue], Any],
        *,
        safe_result: bool = False,
    ) -> Registration:
        check_id(identifier, versioned=True)
        if (
            not self.scope.active
            or not identifier.startswith(self.owner + ".")
            or identifier in self.host.operations
            or not callable(callback)
            or inspect.iscoroutinefunction(callback)
            or len(inspect.signature(callback).parameters) != 1
            or type(safe_result) is not bool
        ):
            raise AppError("invalid_registration")
        self.host.operations[identifier] = PrivateOperation(
            self.owner, callback, safe_result
        )

        def dispose() -> None:
            self.host.operations.pop(identifier, None)

        return self.scope.own(Registration(dispose))

    async def invoke(self, operation: str, handle: PrivateHandle) -> Any:
        self.host.prune()
        grant = self.host.handles.get(handle)
        registered = self.host.operations.get(operation)
        if (
            not self.scope.active
            or grant is None
            or registered is None
            or grant.grantee != self.owner
            or (
                grant.operation != operation
                and not (
                    grant.operation is None
                    and grant.owner == self.owner
                    and registered.owner == self.owner
                )
            )
        ):
            raise AppError("permission_denied")
        result = await self.host.execute(lambda: registered.callback(grant.value))
        # Recheck revocation/expiry after work before releasing any result.
        self.host.prune()
        if handle not in self.host.handles or not self.scope.active:
            raise AppError("permission_denied")
        if registered.safe_result:
            return self.host.codec.decode(self.host.codec.encode(result))
        return self.hold(
            result if isinstance(result, SecretValue) else SecretValue(result)
        )

    async def credential(self, name: str) -> PrivateHandle:
        check_id(name)

        def read() -> SecretValue:
            backend = self.host.backend or native_backend()
            value = backend.get_password(f"{self.host.app_id}:{self.owner}", name)
            if value is None:
                raise ValueError
            return SecretValue(value)

        value = await self.host.execute(read)
        return self.hold(value)

    async def set_credential(self, name: str, value: SecretValue) -> None:
        check_id(name)
        if (
            not isinstance(value, SecretValue)
            or type(value.reveal()) is not str
            or not self.scope.active
        ):
            raise AppError("permission_denied")

        def write() -> None:
            backend = self.host.backend or native_backend()
            backend.set_password(
                f"{self.host.app_id}:{self.owner}", name, value.reveal()
            )

        await self.host.execute(write)
