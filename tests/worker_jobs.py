"""Importable synthetic jobs for spawn tests."""

import asyncio
import os
import threading
import time


def identity(payload):
    return {
        "pid": os.getpid(),
        "thread": threading.get_ident(),
        "value": payload["value"],
    }


def delayed(payload):
    time.sleep(payload["delay"])
    return payload


async def async_identity(payload):
    await asyncio.sleep(0)
    return payload


def crashing(payload):
    raise ValueError("SYNTHETIC-PRIVATE-SENTINEL")
