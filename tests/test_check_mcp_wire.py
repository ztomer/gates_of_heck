"""checks/check_mcp_wire.py: an MCP server judged by what it says on the wire (queue MCP.G).

Every rule is calibrated by a planted server (tests/fixtures/mcp_wire/server.py) that breaks it
ONCE and must go red for its NAMED reason, beside one compliant server that must go green. The
newest version comes from the checker itself, so a bump moves this suite with it.
"""

import contextlib
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time

import pytest
from conftest import REPO_ROOT, write

CHECK = REPO_ROOT / "checks" / "check_mcp_wire.py"
SERVER = REPO_ROOT / "tests" / "fixtures" / "mcp_wire" / "server.py"

_spec = importlib.util.spec_from_file_location("check_mcp_wire", CHECK)
mw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mw)
NEWEST = mw.NEWEST_PROTOCOL

GOOD = {
    "name": "read_thing",
    "inputSchema": {"type": "object"},
    "annotations": {"readOnlyHint": True, "destructiveHint": False},
    "outputSchema": {"type": "object", "properties": {}},
}


def tool(**changes):
    t = json.loads(json.dumps(GOOD))
    for key, value in changes.items():
        if value is None:
            t.pop(key, None)
        else:
            t[key] = value
    return t


def server_line(tmp_path, name, **spec):
    spec.setdefault("versions", [NEWEST])
    spec.setdefault("tools", [GOOD])
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    return " ".join(shlex.quote(s) for s in (sys.executable, str(SERVER), str(path)))


def gate(repo, *lines, extra=""):
    servers = "\n".join(lines)
    write(repo, ".gatesrc", f"GOH_MCP_SERVERS='{servers}'\nGOH_MCP_TIMEOUT=20\n{extra}")
    return subprocess.run(
        [sys.executable, str(CHECK), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def red(r, *reasons):
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    for reason in reasons:
        assert reason in out, (reason, out)
    return out


def green(r):
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "every rule holds" in out, out
    return out


def test_a_compliant_modern_server_is_green(repo, tmp_path):
    green(gate(repo, server_line(tmp_path, "good", tools=[GOOD, tool(name="b")], pages=2)))


def test_a_legacy_server_answering_the_newest_version_is_green(repo, tmp_path):
    green(gate(repo, server_line(tmp_path, "legacy", mode="legacy", version=NEWEST)))


def test_protocol_an_older_negotiated_version_is_red(repo, tmp_path):
    red(
        gate(repo, server_line(tmp_path, "old", mode="legacy", version="2025-11-25")),
        f"protocol: negotiated 2025-11-25, the newest the house knows is {NEWEST}",
    )


def test_protocol_a_discovery_without_the_newest_version_is_red(repo, tmp_path):
    line = server_line(tmp_path, "nonew", versions=["2025-11-25"], version="2025-11-25")
    red(gate(repo, line), "protocol: negotiated 2025-11-25")


def test_annotations_absent_is_red(repo, tmp_path):
    red(
        gate(repo, server_line(tmp_path, "bare", tools=[tool(annotations=None)])),
        "tool read_thing: annotations: none",
    )


def test_annotations_a_hint_left_unstated_is_red_by_name(repo, tmp_path):
    half = tool(annotations={"readOnlyHint": True})
    red(
        gate(repo, server_line(tmp_path, "half", tools=[half])),
        "tool read_thing: annotations: destructiveHint is not stated as a boolean",
    )
    out = red(
        gate(
            repo,
            server_line(
                tmp_path,
                "str",
                tools=[tool(annotations={"readOnlyHint": "yes", "destructiveHint": False})],
            ),
        )
    )
    assert "readOnlyHint is not stated as a boolean" in out, out


def test_destructive_a_destructive_tool_listed_by_default_is_red(repo, tmp_path):
    boom = tool(name="wipe", annotations={"readOnlyHint": False, "destructiveHint": True})
    red(
        gate(repo, server_line(tmp_path, "boom", tools=[GOOD, boom])),
        "tool wipe: destructive: destructiveHint true and listed by default",
    )


def test_output_schema_absent_is_red(repo, tmp_path):
    red(
        gate(repo, server_line(tmp_path, "noschema", tools=[tool(outputSchema=None)])),
        "tool read_thing: outputSchema: none",
    )


def test_output_schema_not_an_object_is_red(repo, tmp_path):
    red(
        gate(repo, server_line(tmp_path, "arr", tools=[tool(outputSchema={"type": "array"})])),
        'tool read_thing: outputSchema: type must be "object"',
    )


def test_unstructured_an_exempt_reply_is_green_and_the_list_is_a_ratchet(repo, tmp_path):
    prose = tool(name="describe", outputSchema=None)
    line = server_line(tmp_path, "prose", tools=[GOOD, prose])
    green(gate(repo, line, extra="GOH_MCP_UNSTRUCTURED='describe: its reply is prose'\n"))
    red(
        gate(repo, line, extra="GOH_MCP_UNSTRUCTURED='describe: prose\ngone: removed'\n"),
        "GOH_MCP_UNSTRUCTURED: gone: stale -- no server lists it",
    )
    red(
        gate(repo, line, extra="GOH_MCP_UNSTRUCTURED='describe: prose\nread_thing: x'\n"),
        "GOH_MCP_UNSTRUCTURED: read_thing: stale -- it declares an outputSchema now",
    )
    r = gate(repo, line, extra="GOH_MCP_UNSTRUCTURED='describe'\n")
    assert r.returncode == 2 and "has no reason" in r.stdout + r.stderr, r.stdout + r.stderr


def test_every_server_is_judged_and_the_red_one_is_named(repo, tmp_path):
    good = server_line(tmp_path, "one")
    bad = server_line(tmp_path, "two", tools=[tool(annotations=None)])
    out = red(gate(repo, good, bad), "two.json")
    assert "one.json -- 1 tool(s), every rule holds" in out, out


def test_a_server_that_asks_the_client_something_is_answered(repo, tmp_path):
    green(gate(repo, server_line(tmp_path, "ask", ask=True)))


@contextlib.contextmanager
def _reap_after(*pidfiles):
    """The test OWNS the fixture's sleepers: whatever the verdict, any still alive is killed."""
    try:
        yield
    finally:
        for f in pidfiles:
            if f.exists() and not _gone(int(f.read_text()), wait=0):
                os.kill(int(f.read_text()), signal.SIGKILL)


def _gone(pid, wait=5.0):
    end = time.monotonic() + wait
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def test_a_silent_server_is_red_within_its_timeout_and_is_reaped(repo, tmp_path):
    pidfile = tmp_path / "hang.pid"
    line = server_line(tmp_path, "hang", hang=True, child=str(pidfile))
    write(repo, ".gatesrc", f"GOH_MCP_SERVERS='{line}'\nGOH_MCP_TIMEOUT=2\n")
    with _reap_after(pidfile):
        r = subprocess.run(
            [sys.executable, str(CHECK), str(repo)],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        red(r, "wire: no answer to server/discover within 2s")
        assert _gone(int(pidfile.read_text())), "the hung server's child outlived the gate"


def test_a_green_server_is_reaped_too_even_what_left_its_group(repo, tmp_path):
    child, detached = tmp_path / "c.pid", tmp_path / "d.pid"
    line = server_line(tmp_path, "kids", child=str(child), detached=str(detached))
    with _reap_after(child, detached):
        green(gate(repo, line))
        assert _gone(int(child.read_text())), "a child in the server's group outlived the gate"
        assert _gone(int(detached.read_text())), "a detached child outlived the gate"


def test_a_server_that_does_not_start_is_red_with_its_stderr(repo):
    red(
        gate(repo, "echo no-such-server-here >&2; exit 3"),
        "wire: the server exited (status 3) before answering server/discover",
        "no-such-server-here",
    )


def test_a_server_that_exited_before_the_write_is_named_as_exited(tmp_path):
    """The race the test above lost under load, made certain: the server is gone before the
    request is written, so the write meets a broken pipe -- and the verdict is still the exit."""
    session = mw.Session("exit 3", tmp_path, 20)
    try:
        session.proc.wait(timeout=10)
        with pytest.raises(mw.WireError) as raised:
            session.request("server/discover", {})
        assert "the server exited (status 3) before answering server/discover" in str(raised.value)
    finally:
        session.close()


def test_the_server_runs_with_a_scratch_home_and_never_the_owners(repo, tmp_path):
    seen = tmp_path / "seen"
    line = server_line(tmp_path, "home")
    r = gate(repo, f'printf %s "$GOH_MCP_SCRATCH" > {shlex.quote(str(seen))}; exec {line}')
    green(r)
    home = seen.read_text()
    assert "goh-mcp-wire." in home and not os.path.exists(home), home


def test_no_servers_named_is_a_precondition_not_a_pass(repo):
    write(repo, ".gatesrc", "GOH_MAX_LINES=500\n")
    r = subprocess.run(
        [sys.executable, str(CHECK), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 2 and "no GOH_MCP_SERVERS" in r.stderr, r.stderr
    write(repo, ".gatesrc", "GOH_MCP_SERVERS='true'\nGOH_MCP_TIMEOUT=soon\n")
    r = subprocess.run(
        [sys.executable, str(CHECK), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 2 and "GOH_MCP_TIMEOUT" in r.stderr, r.stderr


def test_structural_full_runs_the_gate_when_a_repo_opts_in(repo, tmp_path):
    """The opt-in is the key alone: `structural.sh --full` runs the gate and its red is the
    gate's red. --staged never starts a server."""
    from conftest import run_gate

    line = server_line(tmp_path, "boom", tools=[tool(annotations=None)])
    write(repo, ".gatesrc", f"GOH_MCP_SERVERS='{line}'\nGOH_MCP_TIMEOUT=20\n")
    r = run_gate(repo, "gates/structural.sh", "--full")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "tool read_thing: annotations: none" in r.stdout + r.stderr, r.stdout + r.stderr


def test_structural_full_is_fail_fast_unless_asked_to_keep_going(repo):
    """Found writing the MCP hook: `[ -n "$KEEP_GOING" ]` held for 0 as well as 1, so every
    `structural.sh --full` ran keep-going and the fail-fast default (BACKLOG 6.16) never ran.
    Two INDEPENDENT reds (tests/test_structural_keep_going.py): fail-fast names only the first."""
    from conftest import commit_all, run_gate
    from test_structural_keep_going import EARLY_LABEL, LATE_LABEL, TWO_REDS

    for rel, body in TWO_REDS.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    commit_all(repo)
    fast = run_gate(repo, "gates/structural.sh", "--full")
    out = fast.stdout + fast.stderr
    assert fast.returncode == 1 and f"{EARLY_LABEL} failed" in out, out
    assert f"{LATE_LABEL} failed" not in out, out
    every = run_gate(repo, "gates/structural.sh", "--full", "--keep-going")
    out = every.stdout + every.stderr
    assert every.returncode == 1 and f"{LATE_LABEL} failed" in out, out
