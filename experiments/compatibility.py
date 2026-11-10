"""Public wheel metadata and local versions only; no credentials or app payloads."""

import importlib.metadata
import json
import platform
import sys
import urllib.request

if __name__ == "__main__":
    print("Local:", platform.system(), platform.machine(), sys.version.split()[0])
    for name in ("imgui-bundle", "pyzmq", "msgpack", "packaging", "keyring", "duckdb"):
        try:
            print(name, importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            print(name, "not installed")
    with urllib.request.urlopen(
        "https://pypi.org/pypi/imgui-bundle/1.93.0/json", timeout=15
    ) as response:
        data = json.load(response)
    files = [
        entry["filename"]
        for entry in data["urls"]
        if entry["filename"].endswith(".whl")
    ]
    for target in ("macosx", "manylinux", "win"):
        matching = [name for name in files if target in name and "cp314" in name]
        print(target, "CPython 3.14 wheels:", len(matching))
        for name in matching:
            print(" ", name)
