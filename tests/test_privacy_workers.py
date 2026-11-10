from __future__ import annotations

import asyncio
import os
import threading

import pytest

from mkapp import Limits
from mkapp.runtime.codec import Codec
from mkapp.runtime.privacy import PrivateService, PrivateServiceHost
from mkapp.runtime.workers import WorkerPool, WorkerService
from mkapp.sdk import AppError, PrivateHandle, ResourceScope, SecretValue


async def test_private_grants_expiry_revocation_and_no_ordinary_serialization():
    now = [0.0]
    host = PrivateServiceHost("test.app", Limits(), clock=lambda: now[0])
    owner_scope, consumer_scope = ResourceScope(), ResourceScope()
    owner = PrivateService(host, "test.owner", owner_scope)
    consumer = PrivateService(host, "test.consumer", consumer_scope)
    sentinel = SecretValue("SYNTHETIC-PRIVATE-SENTINEL")
    try:
        owner.register(
            "test.owner.length.v1",
            lambda v: {"length": len(v.reveal())},
            safe_result=True,
        )
        owner.register("test.owner.copy.v1", lambda v: v)
        handle = owner.hold(sentinel)
        assert "SYNTHETIC" not in repr(sentinel) + repr(handle)
        for value in (sentinel, handle, {"nested": [handle]}):
            with pytest.raises(AppError):
                Codec(Limits()).encode(value)
        with pytest.raises(AppError, match="permission_denied"):
            await consumer.invoke("test.owner.length.v1", handle)
        grant = owner.grant(handle, "test.consumer", "test.owner.length.v1", ttl=1)
        assert await consumer.invoke("test.owner.length.v1", grant) == {"length": 26}
        with pytest.raises(AppError):
            await consumer.invoke("test.owner.copy.v1", grant)
        assert isinstance(
            await owner.invoke("test.owner.copy.v1", handle), PrivateHandle
        )
        now[0] = 2
        with pytest.raises(AppError):
            await consumer.invoke("test.owner.length.v1", grant)
        grant = owner.grant(handle, "test.consumer", "test.owner.length.v1")
        owner_scope.close()
        with pytest.raises(AppError):
            await consumer.invoke("test.owner.length.v1", grant)
    finally:
        await host.close()
    assert not host.handles


async def test_backend_failure_is_closed_and_sanitized():
    class Locked:
        def get_password(self, *args):
            raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")

        def set_password(self, *args):
            raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")

    host = PrivateServiceHost("test.app", Limits(), backend=Locked())
    private = PrivateService(host, "test.owner", ResourceScope())
    try:
        with pytest.raises(AppError, match="feature_unavailable") as error:
            await private.credential("test.password")
        assert "SYNTHETIC" not in str(error.value)
        with pytest.raises(AppError, match="feature_unavailable"):
            await private.set_credential("test.password", SecretValue("SYNTHETIC"))
        assert not host.handles
    finally:
        await host.close()


async def test_workers_explicit_domains_and_spawned_process():
    pool = WorkerPool(Limits())
    service = WorkerService(pool, "test.worker", ResourceScope())
    try:
        for kind in ("thread", "process"):
            service.register(
                f"test.worker.{kind}", "tests.worker_jobs:identity", kind=kind
            )
            result = await service.submit(f"test.worker.{kind}", {"value": 42})
            assert result["value"] == 42
            if kind == "thread":
                assert result["pid"] == os.getpid()
                assert result["thread"] != threading.get_ident()
            else:
                assert result["pid"] != os.getpid()
        service.register(
            "test.worker.async", "tests.worker_jobs:async_identity", kind="async"
        )
        assert await service.submit("test.worker.async", {"a": None}) == {"a": None}
        service.register(
            "test.worker.crash", "tests.worker_jobs:crashing", kind="process"
        )
        with pytest.raises(AppError, match="internal_error"):
            await service.submit("test.worker.crash", {})
        with pytest.raises(AppError):
            await service.submit("test.worker.thread", SecretValue("SYNTHETIC"))
    finally:
        await pool.close()
    assert not pool.shutdown_timeout


async def test_timed_out_thread_retains_admission_until_actual_completion():
    pool = WorkerPool(Limits())
    service = WorkerService(pool, "test.worker", ResourceScope())
    service.register("test.worker.slow", "tests.worker_jobs:delayed", kind="thread")
    try:
        with pytest.raises(AppError, match="timeout"):
            await service.submit("test.worker.slow", {"delay": 0.1}, timeout_ms=5)
        with pytest.raises(AppError, match="busy"):
            await service.submit("test.worker.slow", {"delay": 0})
        await asyncio.sleep(0.15)
        assert await service.submit("test.worker.slow", {"delay": 0}) == {"delay": 0}
    finally:
        await pool.close()
