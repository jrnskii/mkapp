from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from mkapp import AppConfig, Limits
from mkapp.runtime.bus import ActionService, Bus, EventService
from mkapp.sdk import (
    Action,
    ActionContext,
    ActionRef,
    AppError,
    BaseEvents,
    ResourceScope,
    action,
    contract,
    on,
)


@contract
class Request:
    value: int = 7


@contract
class Response:
    value: int
    source: str


@action("test.provider.echo.v1")
async def Echo(request: Request, ctx: ActionContext) -> Response:
    await asyncio.sleep(0)
    return Response(request.value, ctx.source)


async def fixture_bus(limits=None):
    bus = Bus(
        AppConfig(
            "test.app", limits=limits or Limits(), grants={"test.consumer": (Echo.id,)}
        )
    )
    services = {}
    for owner in ("test.provider", "test.consumer"):
        bus.add_owner(owner)
        bus.ready.add(owner)
        services[owner] = ActionService(bus, owner, ResourceScope())
    return bus, services


async def test_round_trip_normalized_references_and_validation():
    bus, services = await fixture_bus()
    try:
        services["test.provider"].register(Echo)
        caller = services["test.consumer"]
        for target in (Echo, Echo.id, ActionRef(Echo.id)):
            assert await caller.call(target, {}) == {
                "value": 7,
                "source": "test.consumer",
            }
        for payload in ({"value": True}, {"extra": 1}, {"value": "bad"}):
            with pytest.raises(AppError, match="invalid_payload"):
                await caller.call(Echo, payload)
        with pytest.raises(AppError, match="unknown_action"):
            await caller.call("test.missing.v1", {})
        with pytest.raises(AppError, match="invalid_registration"):
            caller.register(Echo)
    finally:
        await bus.close()
    assert bus.context.closed


async def test_concurrency_uses_distinct_contexts_and_one_instance():
    seen, instances = [], []

    class Concurrent(Action):
        id = "test.concurrent.v1"
        input = output = staticmethod(lambda v: v)
        max_concurrency = 2

        def __init__(self):
            instances.append(self)

        async def handle(self, payload, ctx):
            seen.append(ctx)
            await asyncio.sleep(0.01)
            return {"source": ctx.source, "value": payload["value"]}

    bus, services = await fixture_bus()
    try:
        provider = services["test.provider"]
        provider.register(Concurrent)
        results = await asyncio.gather(
            *(provider.call(Concurrent, {"value": n}) for n in range(2))
        )
        assert [r["value"] for r in results] == [0, 1]
        assert len(instances) == 1
        assert seen[0] is not seen[1]
        assert seen[0].correlation_id != seen[1].correlation_id
    finally:
        await bus.close()


async def test_pressure_timeout_and_no_automatic_retry():
    started = asyncio.Event()
    release = asyncio.Event()
    effects = []

    class Slow(Action):
        id = "test.slow.v1"
        input = output = staticmethod(lambda v: v)

        async def handle(self, payload, ctx):
            effects.append(1)
            started.set()
            await release.wait()
            return payload

    bus, services = await fixture_bus(Limits(pending=2))
    try:
        provider = services["test.provider"]
        provider.register(Slow)
        first = asyncio.create_task(provider.call(Slow, {}, timeout_ms=100))
        await started.wait()
        with pytest.raises(AppError, match="busy"):
            await provider.call(Slow, {})
        with pytest.raises(AppError, match="timeout"):
            await first
        assert effects == [1]
        release.set()
        await asyncio.sleep(0)
    finally:
        await bus.close()


@pytest.mark.parametrize("patch", [{"source": "test.forged"}, {"protocol": 2}])
async def test_forged_source_and_version(patch):
    bus, services = await fixture_bus()
    client = bus.clients["test.provider"]
    try:
        services["test.provider"].register(Echo)
        correlation = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        client.pending[correlation] = future
        message = {
            "protocol": 1,
            "kind": "request",
            "correlation_id": correlation,
            "source": client.owner,
            "action": Echo.id,
            "payload": {},
            "deadline": time.monotonic() + 1,
        }
        message.update(patch)
        await client.socket.send(bus.codec.encode(message))
        with pytest.raises(AppError, match="invalid_message"):
            await asyncio.wait_for(future, 1)
        client.pending.pop(correlation)
    finally:
        await bus.close()


async def test_decorated_events_explicit_activation_and_host_only():
    bus, services = await fixture_bus()
    scope = ResourceScope()
    events = EventService(bus, "test.consumer", scope)
    received = asyncio.Event()

    @on(BaseEvents.START)
    async def handler(payload):
        assert payload == {}
        received.set()

    try:
        assert not bus.subscriptions
        events.subscribe(handler)
        events.validate()
        await events.activate()
        await bus.publish("host", BaseEvents.START, {})
        await asyncio.wait_for(received.wait(), 1)
        with pytest.raises(AppError, match="permission_denied"):
            await events.publish(BaseEvents.START, {})
        scope.close()
        assert not bus.subscriptions[BaseEvents.START]
    finally:
        await bus.close()


async def test_invalid_action_metadata_and_schema_rejected_before_use():
    bus, services = await fixture_bus()
    try:
        for cls in (Action, type("Missing", (Action,), {"id": "test.missing.v1"})):
            with pytest.raises(AppError, match="invalid_registration"):
                services["test.provider"].register(cls)

        @action("test.invalid.v1")
        async def invalid(request: int, ctx: ActionContext) -> Response:
            return Response(1, "test")

        with pytest.raises(AppError, match="invalid_registration"):
            services["test.provider"].register(invalid)
    finally:
        await bus.close()
