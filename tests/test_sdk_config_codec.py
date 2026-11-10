from __future__ import annotations

import dataclasses
import math
import subprocess
import sys

import msgpack
import pytest

from mkapp import AppConfig, Limits
from mkapp.runtime.codec import Codec
from mkapp.runtime.config import atomic_json, preferences
from mkapp.sdk import AppError, Plugin, Registration, ResourceScope, contract


def test_headless_import_has_no_service_side_effects():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, threading; before={t.ident for t in threading.enumerate()}; import mkapp.sdk; assert not any(n in sys.modules for n in ('imgui_bundle', 'zmq', 'keyring')); assert before=={t.ident for t in threading.enumerate()}",
        ],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_contract_scope_and_noop_hooks():
    @contract
    class Data:
        value: int = 1

    assert dataclasses.is_dataclass(Data)
    with pytest.raises(dataclasses.FrozenInstanceError):
        Data().value = 2
    closed = []
    scope = ResourceScope()
    scope.own(Registration(lambda: closed.append(1)))
    scope.own(Registration(lambda: closed.append(2)))
    scope.close()
    scope.close()
    assert closed == [2, 1]
    assert Plugin().prepare(None) is None


@pytest.mark.parametrize(
    "value", [object(), {1: "bad"}, math.inf, math.nan, 2**64, (1, 2)]
)
def test_codec_rejects_unsafe_values(value):
    with pytest.raises(AppError, match="invalid_payload"):
        Codec(Limits()).encode(value)


def test_codec_limits_and_malformed_frames():
    codec = Codec(Limits(frame_bytes=128, items=16, depth=3))
    for value in ([0] * 17, [[[[1]]]], b"x" * 129):
        with pytest.raises(AppError):
            codec.encode(value)
    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(AppError):
        codec.encode(cyclic)
    for frame in (
        b"\x81\xa1a",
        b"\x82\xa1a\x01\xa1a\x02",
        msgpack.packb(msgpack.ExtType(1, b"x")),
        b"x" * 129,
    ):
        with pytest.raises(AppError, match="invalid_message"):
            codec.decode(frame)
    value = {"none": None, "data": [True, 1, 2.0, b"bytes", "λ"]}
    assert codec.decode(codec.encode(value)) == value


def test_config_precedence_and_security_ceiling(tmp_path):
    config = AppConfig("test.app", app_root=tmp_path, toggles={"test.one": False})
    path = tmp_path / "settings.json"
    atomic_json(
        path, {"schema_version": 1, "toggles": {"test.one": True, "test.two": False}}
    )
    merged = preferences(config, path, overrides={"test.two": True})
    assert merged.toggles == {"test.one": True, "test.two": True}
    assert path.stat().st_mode & 0o777 == 0o600
    atomic_json(path, {"schema_version": 99, "toggles": {}})
    with pytest.raises(AppError, match="invalid_config"):
        preferences(config, path)
    with pytest.raises(AppError):
        Limits(pending=257)
    with pytest.raises(AppError, match="feature_unavailable"):
        AppConfig("test.app", extensions=("unknown",))
