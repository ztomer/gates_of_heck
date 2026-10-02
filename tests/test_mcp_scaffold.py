"""mcp_scaffold — the protocol as the LIBRARY sees it, in process.

The wire half (the same protocol driven through a real stdio pipe, where a malformed byte can kill
the serve loop) lives in `test_mcp_scaffold_wire.py`. Split by concern rather than for length alone:
this file is the request/response contract — initialize, tools/list, tools/call, and every shape of
malformed input — and none of it needs a subprocess.
"""

import json
import sys

import pytest

from _mcp_scaffold_kit import mc


def _server():
    s = mc.McpServer(name="test-srv", version="9.9.9")

    @s.tool(
        description="Echo the message back.",
        schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    )
    def echo(message: str) -> str:
        return f"echo: {message}"

    @s.tool(description="Always fails.", schema={"type": "object", "properties": {}})
    def boom() -> str:
        raise RuntimeError("kaboom")

    return s


def test_initialize_returns_protocol_and_server_info():
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert resp["result"]["serverInfo"]["name"] == "test-srv"


def test_initialize_echoes_each_supported_version():
    # Echo-if-supported: every version in HANDSHAKE_VERSIONS is answered back.
    for version in mc.HANDSHAKE_VERSIONS:
        resp = _server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": version},
            }
        )
        assert resp["result"]["protocolVersion"] == version, version


def test_initialize_echoes_new_latest_2026_07_28():
    # SEP-2575: 2026-07-28 asked → 2026-07-28 answered (it is the LATEST).
    assert mc.LATEST == "2026-07-28"
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2026-07-28"},
        }
    )
    assert resp["result"]["protocolVersion"] == "2026-07-28"


def test_initialize_unknown_future_version_falls_back_to_latest():
    # Unknown version (and a missing one) → latest, never an echo of garbage.
    for params in ({"protocolVersion": "2099-01-01"}, {}):
        resp = _server().handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        )
        assert resp["result"]["protocolVersion"] == mc.LATEST == "2026-07-28"


def test_tools_list_exposes_name_description_schema():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    assert tools["echo"]["description"] == "Echo the message back."
    assert tools["echo"]["inputSchema"]["required"] == ["message"]


def test_tool_call_returns_text_content():
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}},
        }
    )
    assert resp["result"]["content"] == [{"type": "text", "text": "echo: hi"}]
    assert resp["result"]["isError"] is False


def test_tool_call_handler_exception_is_isError_not_crash():
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "boom", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "kaboom" in resp["result"]["content"][0]["text"]


def test_unknown_tool_is_invalid_params_error():
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        }
    )
    assert resp["error"]["code"] == mc.INVALID_PARAMS


def test_tool_returning_unserializable_value_is_isError_not_crash():
    # Regression (2026-08-25): json.dumps(object()) raised TypeError OUT of
    # handle_message, killing the serve loop mid-session.
    s = mc.McpServer()

    @s.tool(description="junk", schema={"type": "object", "properties": {}})
    def junk():
        return object()

    resp = s.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "junk", "arguments": {}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "unserializable" in resp["result"]["content"][0]["text"]
    json.dumps(resp)  # the reply itself must go over the wire
    # ...and the session lives on:
    assert s.handle_message({"jsonrpc": "2.0", "id": 10, "method": "ping"})["result"] == {}


def test_unknown_method_is_method_not_found():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 6, "method": "no/such"})
    assert resp["error"]["code"] == mc.METHOD_NOT_FOUND


def test_notification_returns_none_no_response():
    assert (
        _server().handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    )


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
        mc.run_subprocess([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)


def test_safe_subprocess_captures_stderr_and_returncode():
    out = mc.run_subprocess(
        [sys.executable, "-c", "import sys; print('e', file=sys.stderr); sys.exit(3)"]
    )
    assert out.returncode == 3 and "e" in out.stderr


# ── round-3 hardening: NO wire input may kill the serve loop ─────────────────
#
# Each crash shape gets a spec-shaped error AND a session-survives assertion
# (a follow-up ping must still be answered).


def test_unhashable_tool_name_is_invalid_params_not_crash():
    # Regression (2026-08-26): name as a list raised TypeError out of
    # _tools.get() (unhashable) and killed the serve loop mid-session.
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": ["echo"], "arguments": {}},
        }
    )
    assert resp["error"]["code"] == mc.INVALID_PARAMS
    assert "string" in resp["error"]["message"]
    # session lives on
    assert _server().handle_message({"jsonrpc": "2.0", "id": 12, "method": "ping"})["result"] == {}


def test_non_string_arguments_are_invalid_params_not_crash():
    resp = _server().handle_message(
        {
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": "hi"},
        }
    )
    assert resp["error"]["code"] == mc.INVALID_PARAMS


def test_non_dict_params_anywhere_is_error_not_crash():
    # Regression (2026-08-26): a string/list params raised AttributeError at
    # params.get() (:120/:105) out of handle_message.
    s = _server()
    for i, method in enumerate(("initialize", "ping", "tools/list", "tools/call", "no/such")):
        resp = s.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 20 + i,
                "method": method,
                "params": "not an object",
            }
        )
        assert "error" in resp, f"{method}: expected error, got {resp}"
        assert resp["error"]["code"] in (mc.INVALID_REQUEST, mc.INVALID_PARAMS)
    # session lives on
    assert s.handle_message({"jsonrpc": "2.0", "id": 99, "method": "ping"})["result"] == {}


def test_non_string_method_is_invalid_request_not_crash():
    resp = _server().handle_message({"jsonrpc": "2.0", "id": 30, "method": 42})
    assert resp["error"]["code"] == mc.INVALID_REQUEST


def test_batch_array_is_explicitly_refused_naming_batch_support():
    # ONE documented choice: JSON-RPC 2.0 batch support absent → refusal,
    # never a silent per-message misread.
    resp = _server().handle_message(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        ]
    )
    assert resp["error"]["code"] == mc.INVALID_REQUEST
    assert "batch" in resp["error"]["message"].lower()


def test_id_null_present_is_a_request_answered_with_null_id():
    # Spec note: "id" PRESENT (even null) is a request → respond with id null.
    # Only an ABSENT "id" member is a notification.
    resp = _server().handle_message({"jsonrpc": "2.0", "id": None, "method": "ping"})
    assert resp is not None
    assert resp["id"] is None
    assert resp["result"] == {}
    # ...and the absent-id form stays a notification:
    assert _server().handle_message({"jsonrpc": "2.0", "method": "ping"}) is None


def test_nan_and_infinity_ids_are_sanitized_not_echoed():
    # json.loads accepts bare NaN/Infinity tokens; echoing them back would
    # put non-conformant tokens on the wire. Sanitized to null.
    for bad in (float("nan"), float("inf"), float("-inf")):
        resp = _server().handle_message({"jsonrpc": "2.0", "id": bad, "method": "ping"})
        assert resp["id"] is None
        json.dumps(resp)  # reply must be wire-safe
