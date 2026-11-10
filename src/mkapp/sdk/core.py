"""Side-effect-free plugin contracts. Contexts are supplied by the host."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Callable, Protocol, TypeVar, dataclass_transform


class AppError(Exception):
    """Public errors expose a fixed code, never the underlying exception text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Registration:
    """Runtime-thread-owned, idempotent disposal handle."""

    def __init__(self, dispose: Callable[[], None]) -> None:
        self._dispose: Callable[[], None] | None = dispose

    def close(self) -> None:
        if self._dispose is not None:
            dispose, self._dispose = self._dispose, None
            dispose()


class ResourceScope:
    """Runtime-owned resources, disposed in reverse order without super hooks."""

    def __init__(self) -> None:
        self.active = True
        self.generation = 0
        self._handles: list[Registration] = []

    def own(self, handle: Registration) -> Registration:
        if not self.active:
            handle.close()
            raise AppError("owner_failed")
        if len(self._handles) >= 1024:
            handle.close()
            raise AppError("busy")

        def dispose() -> None:
            try:
                handle.close()
            finally:
                if owned in self._handles:
                    self._handles.remove(owned)

        owned = Registration(dispose)
        self._handles.append(owned)
        return owned

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        self.generation += 1
        for handle in reversed(tuple(self._handles)):
            try:
                handle.close()
            except Exception:
                pass  # Continue releasing unrelated resources; never print raw errors.
        self._handles.clear()


class Plugin:
    """Lifecycle hooks execute on the runtime thread; no GUI calls or blocking I/O."""

    def prepare(self, ctx: PluginContext) -> None:
        pass

    async def start(self, ctx: PluginContext) -> None:
        pass

    async def stop(self, ctx: PluginContext) -> None:
        pass


class Action:
    """One runtime-owned instance per registration; keep invocation data local."""

    id: str
    input: Any
    output: Any
    timeout_ms = 5000
    max_concurrency = 1
    idempotent = False

    async def handle(self, payload: Any, ctx: ActionContext) -> Any:
        raise NotImplementedError


class Window:
    """One GUI-thread instance per open. Host owns outer Begin/End and cleanup."""

    id: str
    title: str
    presentation = "window"

    def on_open(self, ctx: WindowContext) -> None:
        pass

    def render(self, ctx: WindowContext) -> None:
        raise NotImplementedError

    def on_close(self, ctx: WindowContext) -> None:
        pass

    def visible(self, ctx: WindowContext) -> bool:
        """GUI-thread visibility/shortcut hook, evaluated before host Begin."""
        return True


T = TypeVar("T", bound=type)


@dataclass_transform(frozen_default=True)
def contract(cls: T) -> T:
    """Equivalent to dataclass(frozen=True); no transport or registration effects."""
    return dataclass(frozen=True)(cls)  # type: ignore[return-value]


def action(identifier: str) -> Callable[[Callable[..., Any]], type[Action]]:
    """Generate an Action subclass; annotations are checked during registration."""

    def decorate(fn: Callable[..., Any]) -> type[Action]:
        async def handle(self: Action, payload: Any, ctx: ActionContext) -> Any:
            return await fn(payload, ctx)

        return type(
            fn.__name__,
            (Action,),
            {
                "id": identifier,
                "handle": handle,
                "_annotated": staticmethod(fn),
                "__module__": fn.__module__,
                "__doc__": fn.__doc__,
            },
        )

    return decorate


def on(event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach event metadata only. Subscribe explicitly during prepare."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, "__mkapp_event__", event)
        return fn

    return decorate


class BaseEvents:
    START = "mkapp.app.start.v1"
    SHUTDOWN = "mkapp.app.shutdown.v1"
    KILL = "mkapp.app.kill.v1"


@dataclass(frozen=True)
class ActionRef:
    id: str


def action_id(target: str | ActionRef | type[Action]) -> str:
    if isinstance(target, str):
        return target
    if isinstance(target, ActionRef) or (
        isinstance(target, type) and issubclass(target, Action)
    ):
        return target.id
    raise AppError("invalid_payload")


class Actions(Protocol):
    """Runtime thread only. GUI callers use WindowContext.submit."""

    def register(self, cls: type[Action]) -> Registration: ...
    async def call(
        self,
        target: str | ActionRef | type[Action],
        payload: Any,
        *,
        timeout_ms: int = 5000,
    ) -> Any: ...


class Windows(Protocol):
    def register(
        self, cls: type[Window], *, open_on_start: bool = False
    ) -> Registration: ...


class Events(Protocol):
    def register(
        self, identifier: str, validator: Callable[[Any], Any]
    ) -> Registration: ...
    def subscribe(self, event: Any, callback: Any = None) -> Registration: ...
    async def publish(self, event: str, payload: Any) -> None: ...


class Workers(Protocol):
    """Register in prepare; submit on runtime. Job entries must be importable."""

    def register(
        self, name: str, entry: str, *, kind: str, max_concurrency: int = 1
    ) -> Registration: ...
    async def submit(
        self, name: str, payload: Any, *, timeout_ms: int = 5000
    ) -> Any: ...


class Private(Protocol):
    """Direct scoped API; values and handles never enter ordinary messages."""

    def hold(self, value: SecretValue) -> PrivateHandle: ...
    def grant(
        self, handle: PrivateHandle, grantee: str, operation: str, *, ttl: float = 30
    ) -> PrivateHandle: ...
    def register(
        self,
        identifier: str,
        callback: Callable[[SecretValue], Any],
        *,
        safe_result: bool = False,
    ) -> Registration: ...
    async def invoke(self, operation: str, handle: PrivateHandle) -> Any: ...
    async def credential(self, name: str) -> PrivateHandle: ...
    async def set_credential(self, name: str, value: SecretValue) -> None: ...


class SecretValue:
    """Redaction against accidental propagation, not protected memory or a sandbox."""

    __slots__ = ("_value",)

    def __init__(self, value: Any) -> None:
        self._value = value

    def reveal(self) -> Any:
        """Explicit narrow access; copying the raw value defeats SDK protections."""
        return self._value

    def __repr__(self) -> str:
        return "<SecretValue redacted>"

    __str__ = __repr__


class PrivateHandle:
    """Opaque host-table key. Identity and live host grants establish authority."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<PrivateHandle redacted>"

    __str__ = __repr__


@dataclass(frozen=True)
class NamespaceSpec:
    id: str
    validator: Callable[[Any], Any]
    schema_version: int = 1
    max_entries: int = 128
    max_bytes: int = 1_048_576
    ttl: float = 60.0


@dataclass(frozen=True)
class StateSpec:
    key: str
    validator: Callable[[Any], Any]
    readers: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    persist: bool = False
    schema_version: int = 1


class Records(Protocol):
    """Runtime-thread namespace/key registration; mutations use the action bus."""

    def register(self, spec: Any) -> Registration: ...
    async def set(
        self, key: str, value: Any, *, expected_revision: int | None = None
    ) -> Any: ...
    async def get(self, namespace: str, key: str) -> Any: ...
    async def put(
        self, namespace: str, key: str, value: Any, **options: Any
    ) -> Any: ...
    async def delete(self, namespace: str, key: str, **options: Any) -> Any: ...


class Components(Protocol):
    """Register on runtime; invoke render on GUI under caller/instance ID scopes."""

    def register(
        self, identifier: str, renderer: Callable[..., None]
    ) -> Registration: ...
    def render(
        self, identifier: str, instance: str, data: Any, emit: Callable[[Any], None]
    ) -> None: ...


@dataclass(frozen=True)
class CommandField:
    kind: str
    required: bool = True
    default: Any = None
    choices: tuple[str, ...] = ()
    nullable: bool = False


@dataclass(frozen=True)
class CommandSpec:
    id: str
    path: tuple[str, ...]
    action: str
    fields: dict[str, CommandField] = field(default_factory=dict)
    description: str = ""


class Commands(Protocol):
    def register(self, spec: CommandSpec) -> Registration: ...


class Completions(Protocol):
    def register(self, action_id: str) -> Registration: ...


@dataclass(frozen=True)
class Features:
    names: frozenset[str] = frozenset()

    def has(self, name: str) -> bool:
        return name in self.names


@dataclass(frozen=True)
class PluginContext:
    """Runtime-thread services bound to one plugin and its host-owned scope."""

    owner: str
    actions: Actions
    windows: Windows
    events: Events
    workers: Workers
    private: Private
    state: Records
    cache: Records
    local_storage: Records
    components: Components
    commands: Commands
    completions: Completions
    features: Features = field(default_factory=Features)


@dataclass(frozen=True)
class ActionContext:
    """Runtime-thread invocation data; never retain after handle returns."""

    source: str
    owner: str
    correlation_id: str
    deadline: float
    actions: Actions
    workers: Workers


@dataclass(frozen=True)
class WindowContext:
    """GUI-thread context, revoked on close; arguments are immutable snapshots."""

    instance_id: str
    arguments: Any
    submit: Callable[..., Any]
    components: Components


def lazy_imgui() -> Any:
    return import_module("imgui_bundle")
