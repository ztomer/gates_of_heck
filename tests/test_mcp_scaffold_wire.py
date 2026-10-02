"""mcp_scaffold — the stdio wire contract, over a REAL subprocess pipe.

Split from `test_mcp_scaffold.py` by concern rather than for length alone: that file holds the
protocol as the LIBRARY sees it (handle_message, in process); this one holds the protocol as the
CLIENT sees it, where a reply only counts once it has gone out over a pipe and come back parsed.

Both halves matter, and they fail differently: an in-process reply can be perfect while the serve
loop dies on the first malformed byte, so every crash shape here is proven twice — a spec-shaped
error AND a session that still answers the next ping.
"""

import json
import subprocess
import sys

import pytest

from _mcp_scaffold_kit import REPO_ROOT, SERVER, mc


# ---- a subprocess whose tool result cannot be serialized at all ----------------


def test_wire_unserializable_tool_result_then_session_survives(tmp_path):
    script = "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(REPO_ROOT)!r})",
            "from lib.mcp_scaffold import McpServer, serve",
            "s = McpServer()",
            '@s.tool(description="junk", schema={"type": "object", "properties": {}})',
            "def junk():",
            "    return object()",
            "serve(s)",
        ]
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "junk", "arguments": {}},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["id"] == 1
        assert resp["result"]["isError"] is True
        assert "unserializable" in resp["result"]["content"][0]["text"]
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 2
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# ---- the REAL demo server subprocess ------------------------------------------


class DemoServer:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def send_raw(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def recv(self):
        line = self.proc.stdout.readline()
        assert line, "demo server closed stdout (crashed?)"
        return json.loads(line)


@pytest.fixture
def server():
    d = DemoServer()
    yield d
    d.proc.terminate()
    d.proc.wait(timeout=10)


def _initialize(d):
    d.send(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    resp = d.recv()
    assert "result" in resp
    d.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return resp


def test_wire_initialize_list_call_round_trip(server):
    init = _initialize(server)
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"]

    server.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    listed = server.recv()
    names = [t["name"] for t in listed["result"]["tools"]]
    assert "echo" in names

    server.send(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "over-the-wire"}},
        }
    )
    called = server.recv()
    assert called["id"] == 2
    assert called["result"]["content"][0]["text"] == "echo: over-the-wire"
    assert called["result"]["isError"] is False


def test_wire_malformed_json_yields_parse_error_object_not_crash(server):
    server.send_raw("{not json at all")
    resp = server.recv()
    assert resp["error"]["code"] == mc.PARSE_ERROR
    # the server must still be alive and answering afterwards
    server.send({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert "result" in server.recv()


def test_wire_unknown_method_over_the_wire(server):
    _initialize(server)
    server.send({"jsonrpc": "2.0", "id": 8, "method": "resources/list"})
    resp = server.recv()
    assert resp["error"]["code"] == mc.METHOD_NOT_FOUND
    assert resp["id"] == 8


# ---- wire-level proofs of every crash shape ----------------------------------


def test_wire_unhashable_name_then_session_survives(tmp_path):
    script = "\n".join(
        [
            "import sys",
            f"sys.path.insert(0, {str(REPO_ROOT)!r})",
            "from lib.mcp_scaffold import McpServer, serve",
            "s = McpServer()",
            '@s.tool(description="echo", schema={"type": "object", "properties": {}})',
            "def echo():",
            "    return 'ok'",
            "serve(s)",
        ]
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    try:
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": ["echo"], "arguments": {}},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["error"]["code"] == mc.INVALID_PARAMS
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 2
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_wire_invalid_utf8_bytes_yield_parse_error_then_session_survives():
    # Malformed BYTES must get PARSE_ERROR exactly like malformed syntax —
    # and must NOT kill the loop (a strict text stdin is permanently poisoned
    # by one bad byte; serve() reads bytes and decodes per line instead).
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,  # BINARY pipes
    )
    try:
        proc.stdin.write(b"\xff\xfe not utf8\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["error"]["code"] == mc.PARSE_ERROR
        assert "utf" in resp["error"]["message"].lower()
        proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode() + b"\n"
        )
        proc.stdin.flush()
        listed = json.loads(proc.stdout.readline())
        assert "result" in listed  # session survived the bad bytes
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_wire_batch_array_refused_over_the_wire(server):
    server.send_raw(
        '[{"jsonrpc":"2.0","id":1,"method":"ping"},{"jsonrpc":"2.0","id":2,"method":"ping"}]'
    )
    resp = server.recv()
    assert resp["error"]["code"] == mc.INVALID_REQUEST
    assert "batch" in resp["error"]["message"].lower()
    server.send({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert "result" in server.recv()


def test_wire_null_id_answered_with_null_id(server):
    server.send_raw('{"jsonrpc":"2.0","id":null,"method":"ping"}')
    resp = server.recv()
    assert resp["id"] is None
    assert "result" in resp


def test_wire_nan_id_token_sanitized_to_null(server):
    # json.loads decodes bare NaN; the reply must carry null, never NaN.
    server.send_raw('{"jsonrpc":"2.0","id":NaN,"method":"ping"}')
    resp = server.recv()
    assert resp["id"] is None
