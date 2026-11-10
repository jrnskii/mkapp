"""Runtime-thread-owned inproc ROUTER/DEALER bus. No GUI calls."""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import zmq
import zmq.asyncio

from mkapp.sdk import (
    Action,
    ActionContext,
    ActionRef,
    AppError,
    BaseEvents,
    Registration,
    ResourceScope,
    action_id,
)

from .codec import Codec
from .config import AppConfig, check_id
from .schema import action_adapters
from .workers import WorkerPool, WorkerService

_chain: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "action_chain", default=()
)
SAFE_CODES = frozenset(
    {
        "invalid_message",
        "invalid_payload",
        "permission_denied",
        "feature_unavailable",
        "unknown_action",
        "not_ready",
        "busy",
        "timeout",
        "cancelled",
        "owner_failed",
        "conflict",
        "internal_error",
    }
)


def safe_code(error: BaseException) -> str:
    return (
        error.code
        if isinstance(error, AppError) and error.code in SAFE_CODES
        else "internal_error"
    )


@dataclass
class Binding:
    owner: str
    instance: Action
    read: Callable[[Any], Any]
    write: Callable[[Any], Any]
    concurrency: int
    timeout_ms: int
    active: int = 0


class Client:
    def __init__(self, bus: Bus, owner: str) -> None:
        self.bus, self.owner = bus, owner
        self.identity = uuid.uuid4().hex.encode("ascii")
        self.socket = bus.socket(zmq.DEALER)
        self.socket.setsockopt(zmq.IDENTITY, self.identity)
        self.socket.connect(bus.endpoint)
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.lineage: dict[str, tuple[str, ...]] = {}
        self.reader = asyncio.create_task(self.receive())
        self.closed = False

    async def receive(self) -> None:
        while True:
            frame = await self.socket.recv()
            try:
                message = self.bus.codec.decode(frame)
                if message["kind"] == "event":
                    self.bus.deliver_event(
                        self.owner, message["action"], message["payload"]
                    )
                    continue
                future = self.pending.get(message["correlation_id"])
                if future is None or future.done():
                    continue
                if "error" in message:
                    future.set_exception(
                        AppError(
                            message["error"]
                            if message["error"] in SAFE_CODES
                            else "internal_error"
                        )
                    )
                else:
                    future.set_result(message["payload"])
            except Exception:
                continue

    async def call(self, kind: str, target: str, payload: Any, timeout_ms: int) -> Any:
        self.bus.assert_thread()
        if self.closed:
            raise AppError("owner_failed")
        if len(self.pending) >= self.bus.config.limits.pending:
            raise AppError("busy")
        if target in _chain.get() or len(_chain.get()) >= 16:
            raise AppError("invalid_payload")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise AppError("invalid_payload")
        timeout = min(timeout_ms, self.bus.config.limits.timeout_ms) / 1000
        correlation = uuid.uuid4().hex
        message = {
            "protocol": 1,
            "kind": kind,
            "correlation_id": correlation,
            "source": self.owner,
            "action": target,
            "payload": payload,
            "deadline": time.monotonic() + timeout,
        }
        frame = self.bus.codec.encode(message)
        future = asyncio.get_running_loop().create_future()
        self.pending[correlation] = future
        self.lineage[correlation] = _chain.get()
        try:
            try:
                await self.socket.send(frame, flags=zmq.NOBLOCK)
            except zmq.Again:
                raise AppError("busy") from None
            async with asyncio.timeout(timeout):
                return await future
        except (TimeoutError, asyncio.CancelledError) as error:
            cancel = {**message, "kind": "cancel", "payload": {}}
            try:
                await self.socket.send(self.bus.codec.encode(cancel), flags=zmq.NOBLOCK)
            except zmq.ZMQError, AppError:
                pass  # Cancellation is best effort, including control-queue pressure.
            if isinstance(error, TimeoutError):
                raise AppError("timeout") from None
            raise
        finally:
            self.pending.pop(correlation, None)
            self.lineage.pop(correlation, None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.set_exception(AppError("owner_failed"))
        self.socket.close(linger=0)


class Bus:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.thread = threading.get_ident()
        self.codec = Codec(config.limits)
        self.workers = WorkerPool(config.limits)
        self.context = zmq.asyncio.Context()
        self.endpoint = "inproc://mkapp-" + uuid.uuid4().hex
        self.router = self.socket(zmq.ROUTER)
        self.router.setsockopt(zmq.ROUTER_MANDATORY, 1)
        self.router.bind(self.endpoint)
        self.clients: dict[str, Client] = {}
        self.identities: dict[bytes, str] = {}
        self.bindings: dict[str, Binding] = {}
        self.builtin_owners: set[str] = set()
        self.ready: set[str] = set()
        self.starting: set[str] = set()
        self.running: dict[tuple[bytes, str], asyncio.Task[Any]] = {}
        self.task_owners: dict[asyncio.Task[Any], str] = {}
        self.event_contracts: dict[str, tuple[str, Callable[[Any], Any]]] = {
            e: ("host", self.empty_event)
            for e in (BaseEvents.START, BaseEvents.SHUTDOWN, BaseEvents.KILL)
        }
        self.subscriptions: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
        self.active_subscriptions: set[tuple[str, str]] = set()
        self.event_tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self.event_overflow = 0
        self.reader = asyncio.create_task(self.route())
        self.closed = False

    @staticmethod
    def empty_event(value: Any) -> Any:
        if value != {}:
            raise ValueError
        return value

    def assert_thread(self) -> None:
        if threading.get_ident() != self.thread:
            raise AppError("permission_denied")

    def socket(self, kind: int) -> Any:
        socket = self.context.socket(kind)
        socket.setsockopt(zmq.SNDHWM, self.config.limits.pending * 2)
        socket.setsockopt(zmq.RCVHWM, self.config.limits.pending * 2)
        socket.setsockopt(zmq.MAXMSGSIZE, self.config.limits.frame_bytes)
        socket.setsockopt(zmq.LINGER, 0)
        return socket

    def add_owner(self, owner: str) -> None:
        self.assert_thread()
        if owner in self.clients:
            raise AppError("invalid_registration")
        client = Client(self, owner)
        self.clients[owner] = client
        self.identities[client.identity] = owner

    def register(
        self, owner: str, cls: type[Action], scope: ResourceScope
    ) -> Registration:
        self.assert_thread()
        try:
            if (
                not isinstance(cls, type)
                or not issubclass(cls, Action)
                or cls.handle is Action.handle
                or not inspect.iscoroutinefunction(cls.handle)
            ):
                raise ValueError
            identifier = check_id(cls.id, versioned=True)
            if (
                identifier.startswith("mkapp.")
                and (
                    owner not in self.builtin_owners
                    or not identifier.startswith(owner + ".")
                )
            ) or identifier in self.bindings:
                raise ValueError
            concurrency, timeout = cls.max_concurrency, cls.timeout_ms
            if (
                type(concurrency) is not int
                or not 1 <= concurrency <= self.config.limits.concurrency
                or type(timeout) is not int
                or not 1 <= timeout <= self.config.limits.timeout_ms
            ):
                raise ValueError
            if type(cls.idempotent) is not bool:
                raise ValueError
            read, write = action_adapters(cls, self.codec)
            binding = Binding(owner, cls(), read, write, concurrency, timeout)
        except Exception:
            raise AppError("invalid_registration") from None
        self.bindings[identifier] = binding

        def dispose() -> None:
            self.bindings.pop(identifier, None)

        return scope.own(Registration(dispose))

    def permitted(self, source: str, owner: str, target: str) -> bool:
        return (
            source == owner
            or target in self.config.grants.get(source, ())
            or owner in {"mkapp.state", "mkapp.cache", "mkapp.local_storage"}
        )

    async def route(self) -> None:
        while True:
            parts = await self.router.recv_multipart()
            if len(parts) != 2:
                continue
            identity, frame = parts
            source = self.identities.get(identity)
            if source is None:
                continue
            correlation = ""
            try:
                message = self.codec.decode(frame)
                if type(message) is not dict or set(message) != {
                    "protocol",
                    "kind",
                    "correlation_id",
                    "source",
                    "action",
                    "payload",
                    "deadline",
                }:
                    raise AppError("invalid_message")
                correlation = message["correlation_id"]
                if (
                    type(correlation) is not str
                    or len(correlation) != 32
                    or message["protocol"] != 1
                    or type(message["protocol"]) is not int
                    or message["source"] != source
                ):
                    raise AppError("invalid_message")
                try:
                    target = check_id(message["action"], versioned=True)
                except AppError:
                    raise AppError("invalid_message") from None
                if message["kind"] == "cancel":
                    task = self.running.get((identity, correlation))
                    if task is not None:
                        task.cancel()
                    continue
                # Admission requires a host-generated pending request, not submitted source bytes.
                client = self.clients[source]
                future = client.pending.get(correlation)
                if future is None or future.done():
                    continue
                key = (identity, correlation)
                if key in self.running:
                    raise AppError("invalid_message")
                if source not in self.ready | self.starting:
                    raise AppError("not_ready")
                if message["kind"] in ("subscribe", "unsubscribe"):
                    contract = self.event_contracts.get(target)
                    if message["payload"] != {} or contract is None:
                        raise AppError("invalid_payload")
                    if contract[0] != "host" and not self.permitted(
                        source, contract[0], target
                    ):
                        raise AppError("permission_denied")
                    if message["kind"] == "subscribe":
                        self.active_subscriptions.add((source, target))
                    else:
                        self.active_subscriptions.discard((source, target))
                    await self.reply(
                        identity,
                        correlation,
                        payload={"subscribed": message["kind"] == "subscribe"},
                    )
                    continue
                if message["kind"] == "event":
                    await self.publish(source, target, message["payload"])
                    await self.reply(identity, correlation, payload={})
                    continue
                if message["kind"] != "request":
                    raise AppError("invalid_message")
                binding = self.bindings.get(target)
                if binding is None:
                    raise AppError("unknown_action")
                if binding.owner not in self.ready:
                    raise AppError("not_ready")
                if not self.permitted(source, binding.owner, target):
                    raise AppError("permission_denied")
                if (
                    binding.active >= binding.concurrency
                    or sum(
                        b.active
                        for b in self.bindings.values()
                        if b.owner == binding.owner
                    )
                    >= self.config.limits.concurrency
                ):
                    raise AppError("busy")
                deadline = message["deadline"]
                if type(deadline) not in (int, float) or deadline <= time.monotonic():
                    raise AppError("timeout")
                deadline = min(deadline, time.monotonic() + binding.timeout_ms / 1000)
                binding.active += 1
                task = asyncio.create_task(
                    self.execute(
                        identity,
                        correlation,
                        source,
                        target,
                        binding,
                        message["payload"],
                        deadline,
                        client.lineage[correlation],
                    )
                )
                self.running[key] = task
                self.task_owners[task] = binding.owner

                def finished(
                    t: asyncio.Task[Any],
                    k: tuple[bytes, str] = key,
                    b: Binding = binding,
                ) -> None:
                    self.running.pop(k, None)
                    b.active -= 1

                task.add_done_callback(finished)
                task.add_done_callback(lambda t: self.task_owners.pop(t, None))
            except Exception as error:
                if correlation in self.clients[source].pending:
                    await self.reply(identity, correlation, error=safe_code(error))

    async def execute(
        self,
        identity: bytes,
        correlation: str,
        source: str,
        target: str,
        binding: Binding,
        payload: Any,
        deadline: float,
        lineage: tuple[str, ...],
    ) -> None:
        token = _chain.set((*lineage, target))
        try:
            try:
                request = binding.read(payload)
            except Exception:
                raise AppError("invalid_payload") from None
            ctx = ActionContext(
                source,
                binding.owner,
                correlation,
                deadline,
                ActionService(self, binding.owner, None),
                WorkerService(self.workers, binding.owner, None),
            )
            async with asyncio.timeout_at(deadline):
                result = await binding.instance.handle(request, ctx)
            try:
                result = binding.write(result)
                self.codec.encode(result)
            except Exception:
                raise AppError("invalid_payload") from None
            await self.reply(identity, correlation, payload=result)
        except TimeoutError:
            await self.reply(identity, correlation, error="timeout")
        except asyncio.CancelledError:
            await self.reply(identity, correlation, error="cancelled")
        except Exception as error:
            await self.reply(identity, correlation, error=safe_code(error))
        finally:
            _chain.reset(token)

    async def reply(
        self,
        identity: bytes,
        correlation: str,
        *,
        payload: Any = None,
        error: str | None = None,
    ) -> None:
        message = {"protocol": 1, "kind": "reply", "correlation_id": correlation}
        message["error" if error else "payload"] = error or payload
        try:
            await self.router.send_multipart(
                [identity, self.codec.encode(message)], flags=zmq.NOBLOCK
            )
        except zmq.ZMQError, AppError:
            # Explicitly fail accepted replies locally if the bounded transport cannot deliver.
            source = self.identities.get(identity)
            client = self.clients.get(source or "")
            future = client.pending.get(correlation) if client else None
            if future and not future.done():
                future.set_exception(AppError("owner_failed"))

    async def publish(self, owner: str, event: str, payload: Any) -> None:
        contract = self.event_contracts.get(event)
        if contract is None:
            raise AppError("unknown_action")
        if contract[0] != owner:
            raise AppError("permission_denied")
        try:
            payload = contract[1](payload)
            frame = self.codec.encode(
                {"kind": "event", "action": event, "payload": payload, "protocol": 1}
            )
        except Exception:
            raise AppError("invalid_payload") from None
        recipients = {
            o
            for o, _ in self.subscriptions.get(event, ())
            if o in self.ready and (o, event) in self.active_subscriptions
        }
        for recipient in recipients:
            client = self.clients[recipient]
            try:
                await self.router.send_multipart(
                    [client.identity, frame], flags=zmq.NOBLOCK
                )
            except zmq.ZMQError:
                self.event_overflow += 1

    def deliver_event(self, owner: str, event: str, payload: Any) -> None:
        if owner not in self.ready:
            return
        tasks = self.event_tasks.setdefault(owner, set())
        for subscriber, callback in self.subscriptions.get(event, ()):
            if subscriber != owner:
                continue
            if len(tasks) >= self.config.limits.concurrency:
                self.event_overflow += 1
                continue

            async def invoke(fn: Any = callback) -> None:
                try:
                    async with asyncio.timeout(self.config.limits.timeout_ms / 1000):
                        await fn(self.codec.decode(self.codec.encode(payload)))
                except Exception:
                    pass

            task = asyncio.create_task(invoke())
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    async def remove_owner(self, owner: str) -> None:
        self.ready.discard(owner)
        self.starting.discard(owner)
        self.active_subscriptions = {
            s for s in self.active_subscriptions if s[0] != owner
        }
        await self.workers.remove_owner(owner)
        tasks = [
            t
            for (identity, _), t in self.running.items()
            if self.identities.get(identity) == owner
        ]
        tasks.extend(
            t
            for t, provider in self.task_owners.items()
            if provider == owner and t not in tasks
        )
        tasks.extend(self.event_tasks.pop(owner, set()))
        # Provider tasks must also be cancelled before disposing their registrations.
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=self.config.limits.lifecycle_seconds)
        client = self.clients.get(owner)
        if client:
            await client.close()
            self.identities.pop(client.identity, None)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.workers.close()
        for task in self.running.values():
            task.cancel()
        if self.running:
            await asyncio.wait(
                list(self.running.values()),
                timeout=self.config.limits.lifecycle_seconds,
            )
        for owner in list(self.clients):
            await self.remove_owner(owner)
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        self.router.close(linger=0)
        self.context.term()


class ActionService:
    def __init__(self, bus: Bus, owner: str, scope: ResourceScope | None) -> None:
        self.bus, self.owner, self.scope = bus, owner, scope

    def register(self, cls: type[Action]) -> Registration:
        if self.scope is None:
            raise AppError("permission_denied")
        return self.bus.register(self.owner, cls, self.scope)

    async def call(
        self,
        target: str | ActionRef | type[Action],
        payload: Any,
        *,
        timeout_ms: int = 5000,
    ) -> Any:
        return await self.bus.clients[self.owner].call(
            "request", action_id(target), payload, timeout_ms
        )


class EventService:
    def __init__(self, bus: Bus, owner: str, scope: ResourceScope) -> None:
        self.bus, self.owner, self.scope = bus, owner, scope

    def register(
        self, identifier: str, validator: Callable[[Any], Any]
    ) -> Registration:
        self.bus.assert_thread()
        check_id(identifier, versioned=True)
        if (
            (
                identifier.startswith("mkapp.")
                and (
                    self.owner not in self.bus.builtin_owners
                    or not identifier.startswith(self.owner + ".")
                )
            )
            or identifier in self.bus.event_contracts
            or not callable(validator)
        ):
            raise AppError("invalid_registration")
        self.bus.event_contracts[identifier] = (self.owner, validator)

        def dispose() -> None:
            self.bus.event_contracts.pop(identifier, None)

        return self.scope.own(Registration(dispose))

    def subscribe(self, event: Any, callback: Any = None) -> Registration:
        self.bus.assert_thread()
        if callback is None:
            callback = event
            event = getattr(callback, "__mkapp_event__", None)
        check_id(event, versioned=True)
        if (
            not inspect.iscoroutinefunction(callback)
            or len(inspect.signature(callback).parameters) != 1
        ):
            raise AppError("invalid_registration")
        # Contract existence and permission are checked after all prepare hooks finish.
        entry = (self.owner, callback)
        entries = self.bus.subscriptions.setdefault(event, [])
        if entry in entries:
            raise AppError("invalid_registration")
        entries.append(entry)
        return self.scope.own(
            Registration(lambda: entries.remove(entry) if entry in entries else None)
        )

    def validate(self) -> None:
        for event, subscribers in self.bus.subscriptions.items():
            if not any(owner == self.owner for owner, _ in subscribers):
                continue
            contract = self.bus.event_contracts.get(event)
            if contract is None or (
                contract[0] != "host"
                and not self.bus.permitted(self.owner, contract[0], event)
            ):
                raise AppError("permission_denied")

    async def activate(self) -> None:
        """Acknowledge exact subscriptions before owner readiness."""
        self.validate()
        for event, subscribers in self.bus.subscriptions.items():
            if any(owner == self.owner for owner, _ in subscribers):
                await self.bus.clients[self.owner].call(
                    "subscribe", event, {}, self.bus.config.limits.timeout_ms
                )

    async def publish(self, event: str, payload: Any) -> None:
        await self.bus.clients[self.owner].call(
            "event", event, payload, self.bus.config.limits.timeout_ms
        )
