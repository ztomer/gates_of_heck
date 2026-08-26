"""mcp_scaffold — stdio MCP server scaffold, no SDK dependency.

FRAMING: newline-delimited JSON-RPC 2.0 over stdin/stdout. This matches what
the ancestor servers actually speak — zerothunder_mcp_server.py,
necrohand_mcp_server.py and koffee_mcp.py all read/write one JSON object per
newline; NONE of them implements Content-Length framing (Taxes/mcp_server.py
delegates framing to the SDK). Content-Length would only add a second dialect
nobody here speaks.

Error conventions follow koffee_mcp.py: spec JSON-RPC error codes for
protocol-level failures (-32700/-32600/-32601/-32602), while tool-handler
failures come back as a normal result with isError: true (necrohand/koffee
convention) so clients can show the message instead of a protocol abort.

INPUT-SURVIVAL CONTRACT (2026-08-26): NO wire input may kill the serve loop.
Every malformed shape gets a spec-shaped error and the session continues:
malformed BYTES (invalid UTF-8) → -32700, exactly like malformed syntax;
non-object / non-string-method / non-object params → -32600/-32602; a
tools/call with a non-string name or non-object arguments → -32602. Batches
(a JSON array) are explicitly REFUSED with -32600 naming JSON-RPC 2.0 batch
support as absent — one documented refusal, never a silent misread. An "id"
member that is PRESENT is honored even when null (spec note: respond with
id null); ids that decode to NaN/Infinity are sanitized to null so no bare
NaN token ever goes back over the wire.
"""
from __future__ import annotations

import json
import math
import subprocess  # noqa: F401 — TimeoutExpired contract of killtree.run_captured
import sys
from typing import Callable

try:
    from .killtree import run_captured
except ImportError:  # run as a script: script dir is on sys.path
    from killtree import run_captured

PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC 2.0 / MCP error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class SubprocessTimeout(RuntimeError):
    """A tool's child process exceeded its timeout and was killed."""


def _rpc(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id,
            "error": {"code": code, "message": message}}


def _sanitize_id(msg_id):
    """Echo-safe id: NaN/Infinity decode from bare JSON tokens and must never
    be echoed (json.dumps would emit a non-conformant NaN token)."""
    if isinstance(msg_id, float) and not math.isfinite(msg_id):
        return None
    return msg_id


class McpServer:
    """One MCP session: tool registry + JSON-RPC dispatch. Testable without
    any process — feed handle_message() dicts directly."""

    def __init__(self, name: str = "mcp-server", version: str = "0.1.0"):
        self.name = name
        self.version = version
        self._tools: dict[str, dict] = {}

    # -- registration -------------------------------------------------------

    def tool(self, description: str, schema: dict) -> Callable:
        """Register a handler under its own function name.

        @server.tool(description="...", schema={...inputSchema...})
        def echo(message): ...
        """
        def register(fn: Callable) -> Callable:
            name = getattr(fn, "__name__", None)
            if not name:
                raise ValueError("@tool needs a named function")
            if name in self._tools:
                raise ValueError(f"duplicate tool: {name}")
            self._tools[name] = {
                "description": description,
                "inputSchema": schema,
                "handler": fn,
            }
            return fn

        return register

    def tools_list(self) -> list[dict]:
        return [
            {"name": name, "description": spec["description"],
             "inputSchema": spec["inputSchema"]}
            for name, spec in self._tools.items()
        ]

    # -- dispatch -----------------------------------------------------------

    def handle_message(self, message) -> dict | None:
        """Dispatch one decoded JSON-RPC message. Returns the response dict,
        or None for notifications (messages without an "id" member). Never
        raises on malformed input: every bad shape gets a spec-shaped error
        (see module docstring) — the serve loop's life must not depend on
        what a client sends."""
        # Batch refusal — ONE documented choice: JSON-RPC 2.0 batch support
        # is absent; an array is refused as a whole, never silently misread.
        if isinstance(message, list):
            return _error(None, INVALID_REQUEST,
                          "batch requests not supported "
                          "(JSON-RPC 2.0 batch arrays are not implemented)")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            bad_id = message.get("id") if isinstance(message, dict) else None
            return _error(_sanitize_id(bad_id), INVALID_REQUEST,
                          "not a valid JSON-RPC 2.0 message")
        method = message.get("method")
        if not isinstance(method, str):
            return _error(_sanitize_id(message.get("id")), INVALID_REQUEST,
                          "'method' must be a string")
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            return _error(_sanitize_id(message.get("id")), INVALID_PARAMS,
                          "'params' must be an object")
        params = params or {}
        # Spec note honored: id null PRESENT is still a request → respond
        # with id null. Absent "id" is the only notification shape.
        if "id" not in message:
            return None  # notification: never answered
        msg_id = _sanitize_id(message["id"])

        if method == "initialize":
            version = params.get("protocolVersion") or PROTOCOL_VERSION
            return _rpc(msg_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method == "ping":
            return _rpc(msg_id, {})
        if method == "tools/list":
            return _rpc(msg_id, {"tools": self.tools_list()})
        if method == "tools/call":
            return self._call_tool(msg_id, params)
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _call_tool(self, msg_id, params):
        # Defensive boundary: a hostile/buggy client controls every byte of
        # params. Unhashable name (a list) used to raise TypeError out of
        # _tools.get() and kill the serve loop; non-dict arguments raised
        # AttributeError at the ** unpack. Both are INVALID_PARAMS, never
        # crashes.
        name = params.get("name")
        if not isinstance(name, str):
            return _error(msg_id, INVALID_PARAMS,
                          f"tool 'name' must be a string, got {type(name).__name__}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, INVALID_PARAMS,
                          "'arguments' must be an object")
        spec = self._tools.get(name)
        if spec is None:
            return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name}")
        try:
            value = spec["handler"](**args)
        except Exception as exc:  # noqa: BLE001 — tool failure is a result,
            # not a protocol abort (necrohand/koffee isError convention)
            text = f"{type(exc).__name__}: {exc}"
            return _rpc(msg_id, {
                "content": [{"type": "text", "text": text}],
                "isError": True,
            })
        try:
            text = value if isinstance(value, str) else json.dumps(value)
        except (TypeError, ValueError) as exc:
            # Same isError convention as handler failures: an unserializable
            # RESULT is the tool's failure, never a protocol abort — raising
            # here used to kill the serve loop mid-session.
            return _rpc(msg_id, {
                "content": [{"type": "text",
                             "text": f"tool returned an unserializable value: {exc}"}],
                "isError": True,
            })
        return _rpc(msg_id, {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        })


def _iter_lines(stream):
    """Yield lines from a binary stream, or from `.buffer` beneath a text
    stream. Bytes are the honest unit here: a strict TextIOWrapper is
    PERMANENTLY poisoned by one invalid byte (every later readline returns
    ''), so decoding must happen per line in serve(), never inside the
    stream."""
    buf = getattr(stream, "buffer", None)
    src = buf if buf is not None else stream
    while True:
        line = src.readline()
        if not line:
            return
        yield line


def serve(server: McpServer, in_stream=None, out_stream=None) -> int:
    """Newline-delimited loop: one JSON request per line in, one response line
    out. A line that fails to parse — as SYNTAX or as BYTES (invalid UTF-8)
    — gets a PARSE_ERROR object (id null); the loop keeps running so the
    session survives malformed input of either kind."""
    in_stream = in_stream if in_stream is not None else sys.stdin
    out_stream = out_stream if out_stream is not None else sys.stdout
    for raw in _iter_lines(in_stream):
        try:
            # JSON-RPC over stdio is UTF-8 by spec — decode explicitly per
            # line rather than inheriting the ambient text stream's encoding.
            line = (raw.decode("utf-8") if isinstance(raw, bytes) else raw).strip()
        except UnicodeDecodeError as exc:
            response = _error(None, PARSE_ERROR,
                              f"invalid UTF-8 input: {exc}")
        else:
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = _error(None, PARSE_ERROR, "parse error")
            else:
                response = server.handle_message(message)
        if response is not None:
            out_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            out_stream.flush()
    return 0


# ─── safe subprocess helper ───────────────────────────────────────────────


def run_subprocess(argv, *, timeout: float = 30.0, input_text: str | None = None,
                   cwd: str | None = None, env: dict | None = None):
    """Run an EXPLICIT argv with NO shell, hard timeout, captured output.

    argv must be a list of strings (a bare string is rejected outright — that
    shape is how shell injection sneaks back in). Raises SubprocessTimeout on
    timeout; never raises on nonzero exit — callers inspect returncode.
    """
    if isinstance(argv, str) or not all(isinstance(a, str) for a in argv):
        raise TypeError("run_subprocess takes a list of str argv — never a shell string")
    # run_captured: own session + whole-group kill on timeout, so a timed-out
    # tool's background children cannot outlive the call (see lib/killtree.py).
    try:
        return run_captured(
            argv,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input_text=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise SubprocessTimeout(
            f"timed out after {timeout}s: {argv[0]} …"
        ) from exc


# ─── demo server ──────────────────────────────────────────────────────────


def demo_server() -> McpServer:
    """Minimal but complete server: one echo tool."""
    srv = McpServer(name="goh-mcp-demo", version="1.0.0")

    @srv.tool(
        description="Echo the message back prefixed with 'echo: '.",
        schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "text to echo"},
                "shout": {"type": "boolean",
                          "description": "uppercase the echo"},
            },
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    def echo(message: str, shout: bool = False) -> str:
        text = f"echo: {message}"
        return text.upper() if shout else text

    return srv


def demo_main() -> int:
    return serve(demo_server())


if __name__ == "__main__":
    sys.exit(demo_main())
