"""mcp_scaffold — stdio JSON-RPC 2.0 MCP scaffold.

The demo server is exercised over a REAL subprocess pipe: initialize ->
tools/list -> tools/call on the wire, plus malformed-input resilience.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = REPO_ROOT / "lib" / "mcp_scaffold.py"
sys.path.insert(0, str(REPO_ROOT))

from lib import mcp_scaffold as mc  # noqa: E402


# ---- in-process unit tests ---------------------------------------------------


def _server():
    s = mc.McpServer(name="test-srv", version="9.9.9")

    @s.tool(description="Echo the message back.",
            schema={"type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"]})
    def echo(message: str) -> str:
        return f"echo: {message}"

    @s.tool(description="Always fails.", schema={"type": "object", "properties": {}})
    def boom() -> str:
        raise RuntimeError("kaboom")

    return s


def test_initialize_returns_protocol_and_server_info():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-06-18"}})
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert resp["result"]["serverInfo"]["name"] == "test-srv"


def test_tools_list_exposes_name_description_schema():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert tools["echo"]["description"] == "Echo the message back."
    assert tools["echo"]["inputSchema"]["required"] == ["message"]


def test_tool_call_returns_text_content():
    resp = _server().handle_message({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "echo", "arguments": {"message": "hi"}},
    })
    assert resp["result"]["content"] == [{"type": "text", "text": "echo: hi"}]
    assert resp["result"]["isError"] is False


def test_tool_call_handler_exception_is_isError_not_crash():
    resp = _server().handle_message({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "boom", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    assert "kaboom" in resp["result"]["content"][0]["text"]


def test_unknown_tool_is_invalid_params_error():
    resp = _server().handle_message({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    })
    assert resp["error"]["code"] == mc.INVALID_PARAMS


def test_tool_returning_unserializable_value_is_isError_not_crash():
    # Regression (2026-08-25): json.dumps(object()) raised TypeError OUT of
    # handle_message, killing the serve loop mid-session.
    s = mc.McpServer()

    @s.tool(description="junk", schema={"type": "object", "properties": {}})
    def junk():
        return object()

    resp = s.handle_message({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "junk", "arguments": {}},
    })
    assert resp["result"]["isError"] is True
    assert "unserializable" in resp["result"]["content"][0]["text"]
    json.dumps(resp)  # the reply itself must go over the wire
    # ...and the session lives on:
    assert s.handle_message(
        {"jsonrpc": "2.0", "id": 10, "method": "ping"})["result"] == {}


def test_wire_unserializable_tool_result_then_session_survives(tmp_path):
    script = "\n".join([
        "import sys",
        f"sys.path.insert(0, {str(REPO_ROOT)!r})",
        "from lib.mcp_scaffold import McpServer, serve",
        "s = McpServer()",
        '@s.tool(description="junk", schema={"type": "object", "properties": {}})',
        "def junk():",
        "    return object()",
        "serve(s)",
    ])
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )
    try:
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "junk", "arguments": {}},
        }) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        assert resp["id"] == 1
        assert resp["result"]["isError"] is True
        assert "unserializable" in resp["result"]["content"][0]["text"]
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n")
        proc.stdin.flush()
        assert json.loads(proc.stdout.readline())["id"] == 2
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_unknown_method_is_method_not_found():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 6, "method": "no/such"})
    assert resp["error"]["code"] == mc.METHOD_NOT_FOUND


def test_notification_returns_none_no_response():
    assert _server().handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_non_jsonrpc_garbage_is_invalid_request_not_crash():
    resp = _server().handle_message({"hello": "world"})
    assert resp["error"]["code"] == mc.INVALID_REQUEST


# ---- safe subprocess helper --------------------------------------------------


def test_safe_subprocess_runs_explicit_argv_no_shell():
    out = mc.run_subprocess([sys.executable, "-c", "print('argv-ok')"])
    assert out.stdout.strip() == "argv-ok"
    assert out.returncode == 0


def test_safe_subprocess_rejects_string_command_shell_injection_shape():
    with pytest.raises(TypeError):
        mc.run_subprocess("rm -rf /")


def test_safe_subprocess_timeout_raises_named_error():
    with pytest.raises(mc.SubprocessTimeout):
        mc.run_subprocess([sys.executable, "-c", "import time; time.sleep(30)"],
                          timeout=1)


def test_safe_subprocess_captures_stderr_and_returncode():
    out = mc.run_subprocess([sys.executable, "-c",
                             "import sys; print('e', file=sys.stderr); sys.exit(3)"])
    assert out.returncode == 3 and "e" in out.stderr


# ---- wire tests against the REAL demo server subprocess ----------------------


class DemoServer:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
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
    d.send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}})
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

    server.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "echo",
                            "arguments": {"message": "over-the-wire"}}})
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
