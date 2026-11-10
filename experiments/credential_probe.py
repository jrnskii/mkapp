"""Synthetic native-store probe. Never reads or modifies an existing credential."""

import subprocess
import sys
import uuid

from mkapp.runtime.privacy import native_backend

if __name__ == "__main__":
    backend = native_backend()
    service = "example.mkapp.foundation-probe"
    account = uuid.uuid4().hex
    try:
        backend.set_password(service, account, "SYNTHETIC-MKAPP-CREDENTIAL")
        assert backend.get_password(service, account) == "SYNTHETIC-MKAPP-CREDENTIAL"
        source = (
            "from mkapp.runtime.privacy import native_backend; "
            f"assert native_backend().get_password({service!r}, {account!r}) == 'SYNTHETIC-MKAPP-CREDENTIAL'"
        )
        result = subprocess.run(
            [sys.executable, "-c", source], capture_output=True, timeout=15
        )
        assert result.returncode == 0, "same-interpreter credential probe failed"
        print("Native synthetic write/read and same-interpreter access passed")
    finally:
        backend.delete_password(service, account)
