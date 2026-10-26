#!/usr/bin/env python3
"""check_mcp_wire.py -- an MCP server, judged by what it SAYS on the wire, in any language.

    check_mcp_wire.py [repo]      # start every server .gatesrc names, judge each, reap each

WHY (queue MCP.G). Five house repos carry MCP servers in three languages, and every rule the house
holds for them (newest protocol, stated effects, destructive tools behind an opt-in, structured
replies that say their shape) was enforced in each repo by its own tests, or not at all. One gate
that speaks the protocol to the running server judges every one of them the same way, and it reads
the wire, never the source, so the language behind it does not matter.

THE CONVERSATION. Each command is started under `/bin/sh -c` in the repo root, in its OWN session,
with `GOH_MCP_SCRATCH` naming a fresh empty directory (point the server's home at it, so it never
reads the owner's data). The newest protocol has no handshake, so the gate first asks
`server/discover` with the newest version in the request's `_meta`; a server whose discovery lists
that version is spoken to in it. Any other answer falls back to the legacy handshake (`initialize`
asking for the newest, `notifications/initialized`), and the version it answers is the negotiated
one. Then `tools/list`, every page. Nothing else is asked: the gate NEVER calls a tool.

THE RULES, each red with its own reason:
  protocol      the negotiated version is not NEWEST_PROTOCOL.
  annotations   a tool lacks `annotations`, or does not state both readOnlyHint and destructiveHint
                as booleans (the spec's defaults for an unstated hint are the alarming ones).
  destructive   a tool with destructiveHint true is listed when the server is started as the gate
                starts it: a destructive tool must need an explicit opt-in to appear.
  outputSchema  a tool declares no outputSchema, or one whose type is not "object" (the spec's MUST).
                THE CHECKABLE RULE, and why: whether a reply is structured is observable only by
                CALLING the tool, and the gate never calls one (a call can act on the world, and
                readOnlyHint is the very hint being checked, so it cannot be leaned on). So every
                reply is taken as structured -- the house sends structuredContent for every JSON
                reply (the rust-mcp-rmcp skill) -- and a tool whose reply genuinely is not (prose,
                an image) is named in GOH_MCP_UNSTRUCTURED with its reason. That list is a RATCHET:
                an entry no server lists, or whose tool now declares an outputSchema, is red.
Not a rule but still red: a server that does not start, answer, or list in time, and a process the
server left running after it was stopped (the gate reaps the whole group and says what it found).

Keys in `.gatesrc` (docs/config.md):
  GOH_MCP_SERVERS       one stdio start command per line (`#` lines ignored). Setting it is the
                        opt-in: `structural.sh --full` then runs this gate.
  GOH_MCP_UNSTRUCTURED  one `tool: reason` per line, for replies that are not structured.
  GOH_MCP_TIMEOUT       seconds each server gets, start (a `cargo run` build) to its last page.
                        Default 300.

BUMPING THE PROTOCOL. NEWEST_PROTOCOL below is the ONE place goh spells the newest version. When
the spec publishes a newer one (modelcontextprotocol.io's changelog; rmcp's `ProtocolVersion`
KNOWN_VERSIONS, `cargo search rmcp`), change it here and run tests/test_check_mcp_wire.py: the
planted servers take the value from this module, so the suite moves with it, and every consumer's
push goes red until its server speaks the new version -- which is the point.

Exit 0 every server is compliant, 1 a rule is broken (or a server failed), 2 a precondition (no
GOH_MCP_SERVERS, a bad timeout, an exemption with no reason).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import json
import os
import pathlib
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.bounded_run import _alive, _descendants, group_members
from tui.lib import err, info, ok, step

NEWEST_PROTOCOL = "2026-07-28"
CLIENT_INFO = {"name": "goh-mcp-wire", "version": "1"}
KEYS = ("GOH_MCP_SERVERS", "GOH_MCP_UNSTRUCTURED", "GOH_MCP_TIMEOUT")
DEFAULT_TIMEOUT = 300
MAX_PAGES = 100
GRACE = 3.0


class ConfigError(Exception):
    """A precondition the gate cannot judge without: exit 2, never a verdict."""


class WireError(Exception):
    """The server did not hold up its half of the conversation."""


def read_config(root: pathlib.Path) -> dict[str, str]:
    """The keys as `.gatesrc` declares them, sourced by bash as every gate reads it, with the
    environment's own copies dropped (the environment is not configuration)."""
    config = dict.fromkeys(KEYS, "")
    if not (root / ".gatesrc").is_file():
        return config
    program = ". ./.gatesrc >/dev/null 2>&1; " + "; ".join(
        f'printf "%s\\0" "${{{k}:-}}"' for k in KEYS
    )
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    run = subprocess.run(
        ["bash", "-c", program],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    config.update(zip(KEYS, run.stdout.split("\0"), strict=False))
    return config


def _lines(value: str) -> list[str]:
    return [s.strip() for s in value.splitlines() if s.strip() and not s.strip().startswith("#")]


def parse_unstructured(value: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _lines(value):
        name, _, reason = line.partition(":")
        if not reason.strip():
            raise ConfigError(f"GOH_MCP_UNSTRUCTURED entry '{line}' has no reason: `tool: reason`")
        out[name.strip()] = reason.strip()
    return out


def parse_timeout(value: str) -> int:
    if not value.strip():
        return DEFAULT_TIMEOUT
    if not value.strip().isdigit() or int(value) == 0:
        raise ConfigError(f"GOH_MCP_TIMEOUT must be a positive whole number of seconds: '{value}'")
    return int(value)


class Session:
    """One live server: its pipes, its deadline, and the reaping of its whole process group."""

    def __init__(self, command: str, root: pathlib.Path, timeout: int) -> None:
        self.command = command
        self.deadline = time.monotonic() + timeout
        self.timeout = timeout
        self.scratch = tempfile.mkdtemp(prefix="goh-mcp-wire.")
        self.stderr = open(os.path.join(self.scratch, ".stderr"), "w+b")  # noqa: SIM115
        env = foreign_repo_env()
        env["GOH_MCP_SCRATCH"] = os.path.join(self.scratch, "home")
        os.mkdir(env["GOH_MCP_SCRATCH"])
        self.proc = subprocess.Popen(
            ["/bin/sh", "-c", command],
            cwd=root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            start_new_session=True,
        )
        self.pgid = self.proc.pid  # setsid: the group is the leader's pid (lib/killtree.py)
        self.inbox: queue.Queue[bytes | None] = queue.Queue()
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.inbox.put(line)
        self.inbox.put(None)

    def send(self, message: dict) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message).encode() + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            # A server that EXITED closed its stdin too, and which one a writer meets first is a
            # race: under load the exit landed before the request was written, and the verdict
            # flipped from "exited (status 3)" to "closed its stdin" (G17c, a red land). The exit
            # is the fact; the broken pipe is how it was noticed. A server still alive that closed
            # its stdin is the other fact, and says so.
            try:
                code = self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                raise WireError(f"the server closed its stdin ({e}){self.tail()}") from e
            what = message.get("method", "the conversation")
            raise WireError(
                f"the server exited (status {code}) before answering {what}{self.tail()}"
            ) from e

    def request(self, method: str, params: dict) -> dict:
        """Send one request; return its response (result or error), answering anything the server
        asks meanwhile with -32601 so a server-side request cannot wedge the conversation."""
        self.next_id += 1
        want = self.next_id
        self.send({"jsonrpc": "2.0", "id": want, "method": method, "params": params})
        while True:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise WireError(f"no answer to {method} within {self.timeout}s{self.tail()}")
            try:
                line = self.inbox.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                code = self.proc.poll()
                raise WireError(
                    f"the server exited (status {code}) before answering {method}{self.tail()}"
                )
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # not a message; a client drops it too
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:  # the server asks the client something
                self.send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "goh-mcp-wire answers nothing"},
                    }
                )
                continue
            if msg.get("id") == want:
                return msg

    def tail(self, n: int = 12) -> str:
        self.stderr.flush()
        self.stderr.seek(0)
        lines = self.stderr.read().decode("utf-8", "replace").splitlines()[-n:]
        return "".join(f"\n      {s}" for s in lines) if lines else ""

    def close(self) -> list[int]:
        """Stop the server, pass or fail: EOF first (a clean exit), then TERM and KILL the whole
        group and every descendant seen. Returns the pids still alive after all of it."""
        tree = set(_descendants(self.proc.pid)) | set(group_members(self.pgid))
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            pass
        tree |= set(group_members(self.pgid))
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.pgid, sig)
            except OSError:
                pass
            for pid in tree:
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
            end = time.monotonic() + (GRACE if sig == signal.SIGTERM else 1.0)
            while time.monotonic() < end and any(_alive(p) for p in tree | {self.proc.pid}):
                self.proc.poll()
                time.sleep(0.05)
        self.proc.wait()
        self.stderr.close()
        shutil.rmtree(self.scratch, ignore_errors=True)
        return sorted(p for p in tree if _alive(p))


def _meta() -> dict:
    return {
        "io.modelcontextprotocol/protocolVersion": NEWEST_PROTOCOL,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
    }


def negotiate(session: Session) -> tuple[str, bool]:
    """The version the server speaks, and whether requests carry per-request `_meta`."""
    found = session.request("server/discover", {"_meta": _meta()})
    versions = (found.get("result") or {}).get("supportedVersions") or []
    if NEWEST_PROTOCOL in versions:
        return NEWEST_PROTOCOL, True
    init = session.request(
        "initialize",
        {"protocolVersion": NEWEST_PROTOCOL, "capabilities": {}, "clientInfo": CLIENT_INFO},
    )
    if "result" not in init:
        raise WireError(f"initialize was refused: {json.dumps(init.get('error'))}")
    version = str(init["result"].get("protocolVersion", ""))
    session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return version, version == NEWEST_PROTOCOL


def list_tools(session: Session, inline_meta: bool) -> list[dict]:
    tools: list[dict] = []
    cursor = None
    for _ in range(MAX_PAGES):
        params: dict = {"_meta": _meta()} if inline_meta else {}
        if cursor is not None:
            params["cursor"] = cursor
        answer = session.request("tools/list", params)
        if "result" not in answer:
            raise WireError(f"tools/list was refused: {json.dumps(answer.get('error'))}")
        page = answer["result"].get("tools")
        if not isinstance(page, list):
            raise WireError("tools/list answered without a `tools` list")
        tools += [t for t in page if isinstance(t, dict)]
        cursor = answer["result"].get("nextCursor")
        if not cursor:
            return tools
    raise WireError(f"tools/list did not end within {MAX_PAGES} pages")


def judge_tool(tool: dict, unstructured: dict[str, str]) -> list[str]:
    name = str(tool.get("name", "?"))
    found: list[str] = []
    notes = tool.get("annotations")
    if not isinstance(notes, dict):
        found.append(f"tool {name}: annotations: none -- state readOnlyHint and destructiveHint")
        notes = {}
    else:
        for hint in ("readOnlyHint", "destructiveHint"):
            if not isinstance(notes.get(hint), bool):
                found.append(f"tool {name}: annotations: {hint} is not stated as a boolean")
    if notes.get("destructiveHint") is True:
        found.append(
            f"tool {name}: destructive: destructiveHint true and listed by default --"
            " a destructive tool must need an explicit opt-in to appear"
        )
    schema = tool.get("outputSchema")
    if schema is None:
        if name not in unstructured:
            found.append(
                f"tool {name}: outputSchema: none -- declare the reply's shape, or name"
                " the tool in GOH_MCP_UNSTRUCTURED with why its reply is not structured"
            )
    elif not isinstance(schema, dict) or schema.get("type") != "object":
        found.append(f'tool {name}: outputSchema: type must be "object"')
    return found


def judge_server(
    command: str, root: pathlib.Path, timeout: int, unstructured: dict[str, str]
) -> tuple[list[str], list[dict]]:
    session = Session(command, root, timeout)
    found: list[str] = []
    tools: list[dict] = []
    try:
        version, inline = negotiate(session)
        if version != NEWEST_PROTOCOL:
            found.append(
                f"protocol: negotiated {version or 'nothing'}, the newest the house knows"
                f" is {NEWEST_PROTOCOL} (checks/check_mcp_wire.py NEWEST_PROTOCOL)"
            )
        tools = list_tools(session, inline)
        for tool in tools:
            found += judge_tool(tool, unstructured)
    except WireError as e:
        found.append(f"wire: {e}")
    finally:
        left = session.close()
        if left:
            found.append(f"reap: {len(left)} process(es) outlived the server's stop: {left}")
    return found, tools


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = pathlib.Path(args[0] if args else ".").resolve()
    try:
        config = read_config(root)
        servers = _lines(config["GOH_MCP_SERVERS"])
        if not servers:
            raise ConfigError(
                f"no GOH_MCP_SERVERS in {root}/.gatesrc: name each server's stdio"
                " start command, one per line"
            )
        unstructured = parse_unstructured(config["GOH_MCP_UNSTRUCTURED"])
        timeout = parse_timeout(config["GOH_MCP_TIMEOUT"])
    except ConfigError as e:
        err(f"mcp-wire: {e}")
        return 2
    info(f"mcp-wire: {len(servers)} server(s), newest protocol {NEWEST_PROTOCOL}, {timeout}s each")
    failed = 0
    listed: dict[str, dict] = {}
    for command in servers:
        found, tools = judge_server(command, root, timeout, unstructured)
        listed.update({str(t.get("name")): t for t in tools})
        if found:
            failed += 1
            err(f"mcp-wire: {command}")
            for reason in found:
                step(reason)
        else:
            ok(f"mcp-wire: {command} -- {len(tools)} tool(s), every rule holds")
    stale = [
        f"GOH_MCP_UNSTRUCTURED: {n}: stale -- no server lists it"
        for n in unstructured
        if n not in listed
    ]
    stale += [
        f"GOH_MCP_UNSTRUCTURED: {n}: stale -- it declares an outputSchema now"
        for n in unstructured
        if n in listed and "outputSchema" in listed[n]
    ]
    if stale:
        failed += 1
        err("mcp-wire: the unstructured list is a ratchet")
        for reason in stale:
            step(reason)
    if failed:
        err(f"mcp-wire: {failed} finding group(s)")
        return 1
    ok("mcp-wire: every server speaks the newest protocol and states every tool's effect")
    return 0


if __name__ == "__main__":
    sys.exit(main())
