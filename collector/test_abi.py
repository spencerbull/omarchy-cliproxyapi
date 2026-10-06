#!/usr/bin/env python3
"""Exercise the compiled C ABI in isolation using synthetic records, no server."""
import base64
import ctypes as c
import datetime
import json
from pathlib import Path
import sys
import tempfile


class Buffer(c.Structure):
    _fields_ = [("ptr", c.c_void_p), ("length", c.c_size_t)]


Call = c.CFUNCTYPE(c.c_int, c.c_char_p, c.c_void_p, c.c_size_t, c.POINTER(Buffer))
Free = c.CFUNCTYPE(None, c.c_void_p, c.c_size_t)
Shutdown = c.CFUNCTYPE(None)


class Host(c.Structure):
    _fields_ = [("version", c.c_uint32), ("context", c.c_void_p),
                ("call", c.c_void_p), ("free", c.c_void_p)]


class Plugin(c.Structure):
    _fields_ = [("version", c.c_uint32), ("call", Call),
                ("free", Free), ("shutdown", Shutdown)]


lib = c.CDLL(str(Path(sys.argv[1]).resolve()))
lib.cliproxy_plugin_init.argtypes = [c.POINTER(Host), c.POINTER(Plugin)]
host = Host(1, None, None, None)
plugin = Plugin()
assert lib.cliproxy_plugin_init(c.byref(host), c.byref(plugin)) == 0
assert plugin.version == 1


def call(method, request):
    raw = json.dumps(request).encode()
    buffer = Buffer()
    status = plugin.call(method.encode(), raw, len(raw), c.byref(buffer))
    try:
        result = json.loads(c.string_at(buffer.ptr, buffer.length))
    finally:
        plugin.free(buffer.ptr, buffer.length)
    assert status == 0 and result["ok"], result
    return result["result"]


with tempfile.TemporaryDirectory(prefix="omarchy-usage-abi-") as directory:
    config = {"config_yaml": base64.b64encode(f"data_dir: {directory}".encode()).decode()}
    metadata = call("plugin.register", config)
    assert metadata["capabilities"] == {"usage_plugin": True, "management_api": True}
    routes = call("management.register", {"BasePath": "/v0/management"})
    path = "/v0/management/plugins/omarchy-usage/summary"
    assert routes == {"routes": [{"Method": "GET", "Path": path}]}
    event = {"RequestID": "synthetic-execution", "AuthIndex": "0123456789abcdef",
             "Provider": "codex", "RequestedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "Detail": {"InputTokens": 100, "OutputTokens": 20, "TotalTokens": 120,
                        "CachedTokens": 25, "ReasoningTokens": 5},
             "APIKey": "synthetic-never-persist", "AuthID": "synthetic-private-file"}
    call("usage.handle", event)
    call("usage.handle", event)
    for _ in range(2):  # summary reads do not consume records
        response = call("management.handle", {"Method": "GET", "Path": path})
        assert response["StatusCode"] == 200
        result = json.loads(base64.b64decode(response["Body"]))
        account = result["accounts"][0]
        assert account["requests"] == 1
        assert account["tokenMetrics"]["total"] == 120
        assert account["metricSamples"]["total"] == 1
        assert result["health"] == "ok"
    # Rejected C buffers mark coverage partial without dereferencing the pointer.
    for pointer, length in [(None, 1), (None, 4 * 1024 * 1024 + 1)]:
        rejected = Buffer()
        assert plugin.call(b"usage.handle", pointer, length, c.byref(rejected)) == 1
        plugin.free(rejected.ptr, rejected.length)
    response = call("management.handle", {"Method": "GET", "Path": path})
    degraded = json.loads(base64.b64decode(response["Body"]))
    assert degraded["partial"] and degraded["dropped"] == 2
    plugin.shutdown()
    state = Path(directory, "usage.json").read_text()
    assert "synthetic-never-persist" not in state and "synthetic-private-file" not in state
    call("plugin.register", config)
    call("usage.handle", event)
    response = call("management.handle", {"Method": "GET", "Path": path})
    assert json.loads(base64.b64decode(response["Body"]))["accounts"][0]["requests"] == 1
    plugin.shutdown()
print("C ABI registration, usage delivery, authenticated-route declaration, export, dedup and restart passed")
