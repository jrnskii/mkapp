"""Importable bounded jobs; process input/results are MessagePack primitives."""

import time


def sum_squares(payload):
    return {"total": sum(i * i for i in range(payload["count"]))}


def synthetic_io(payload):
    time.sleep(0.01)
    return {"sample": 42}
