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


def test_a_server_whose_stdout_closes_before_it_exits_is_named_with_its_status(tmp_path):
    """The race test_a_server_that_does_not_start_is_red_with_its_stderr lost ("status None"): the
    reader sees EOF on stdout BEFORE the process can be reaped, and the verdict polled once. Made
    certain here -- stdout closes a second before the exit -- and the status is still the exit's."""
    session = mw.Session("exec 1>&-; sleep 1; exit 3", tmp_path, 20)
    try:
        with pytest.raises(mw.WireError) as raised:
            session.request("server/discover", {})
        assert "the server exited (status 3) before answering server/discover" in str(raised.value)
    finally:
        session.close()


def test_a_server_that_closes_stdout_and_lives_on_is_named_at_the_deadline(tmp_path):
    session = mw.Session("exec 1>&-; exec sleep 30", tmp_path, 2)
    try:
        with pytest.raises(mw.WireError) as raised:
            session.request("server/discover", {})
        assert "closed its stdout and did not exit within 2s" in str(raised.value), raised.value
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


# --- the build is not the answer (GOH.MCPWIRELOAD) -----------------------------------------------
# token-pool 751531b's push failed "no answer to server/discover within 300s" at load ~120: its
# server is `cargo run --release`, and the build ran inside the bound meant for the wire answer. A
# planted `cargo` on PATH stands in for the real one: `build` takes FAKE_CARGO_BUILD_S and leaves a
# stamp; `run` builds first when the stamp is missing (as cargo does), then execs what follows `--`.
FAKE_CARGO = r"""#!/bin/sh
d="$FAKE_CARGO_DIR"
echo "$*" >> "$d/calls"
echo "CARGO_HOME=${CARGO_HOME:-} RUSTUP_HOME=${RUSTUP_HOME:-}" >> "$d/env"
case "$1" in
build)
  if [ -n "${FAKE_CARGO_FAIL:-}" ]; then
    echo "error[E0425]: planted build failure" >&2; exit 101
  fi
  echo $$ > "$d/build.pid"
  sleep "$FAKE_CARGO_BUILD_S"; : > "$d/built"; exit 0 ;;
run)
  [ -f "$d/built" ] || { sleep "$FAKE_CARGO_BUILD_S"; : > "$d/built"; }
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done; shift
  exec "$@" ;;
esac
exit 64
"""


def cargo_gate(repo, tmp_path, server, *, timeout, build_s, extra="", env=None, prefix=""):
    """The gate over `cargo run ... -- <planted server>`, with the planted cargo first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "cargo"
    fake.write_text(FAKE_CARGO, encoding="utf-8")
    fake.chmod(0o755)
    line = f"{prefix}cargo run --release --locked --quiet -- {server}"
    write(repo, ".gatesrc", f"GOH_MCP_SERVERS='{line}'\nGOH_MCP_TIMEOUT={timeout}\n{extra}")
    run_env = dict(os.environ)
    run_env.update(env or {})
    run_env["PATH"] = f"{bin_dir}{os.pathsep}{run_env['PATH']}"
    run_env["FAKE_CARGO_DIR"] = str(tmp_path)
    run_env["FAKE_CARGO_BUILD_S"] = str(build_s)
    t0 = time.monotonic()
    r = subprocess.run(
        [sys.executable, str(CHECK), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=run_env,
        timeout=180,
    )
    return r, time.monotonic() - t0


def calls(tmp_path):
    p = tmp_path / "calls"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_a_slow_build_and_a_fast_server_is_green_the_build_is_not_timed_as_the_answer(
    repo, tmp_path
):
    """The build (10 s) is longer than the whole wire bound (6 s); the server answers at once."""
    r, _ = cargo_gate(repo, tmp_path, server_line(tmp_path, "built"), timeout=6, build_s=10)
    green(r)
    assert calls(tmp_path)[0] == "build --release --locked --quiet", calls(tmp_path)


def test_a_built_server_that_never_answers_is_still_red_as_no_answer(repo, tmp_path):
    r, _ = cargo_gate(
        repo, tmp_path, server_line(tmp_path, "mute", hang=True), timeout=2, build_s=0
    )
    out = red(r, "wire: no answer to server/discover within 2s")
    assert "build:" not in out, out


def test_a_failed_build_says_build_with_its_output_and_starts_no_server(repo, tmp_path):
    r, _ = cargo_gate(
        repo,
        tmp_path,
        server_line(tmp_path, "unbuilt"),
        timeout=20,
        build_s=0,
        env={"FAKE_CARGO_FAIL": "1"},
    )
    red(
        r,
        "build: cargo build --release --locked --quiet failed (status 101)",
        "planted build failure",
    )
    assert not any(c.startswith("run") for c in calls(tmp_path)), calls(tmp_path)


def test_a_build_past_its_own_bound_says_build_and_is_reaped(repo, tmp_path):
    r, took = cargo_gate(
        repo,
        tmp_path,
        server_line(tmp_path, "slow"),
        timeout=20,
        build_s=60,
        extra="GOH_MCP_BUILD_TIMEOUT=2\n",
    )
    red(r, "build: cargo build --release --locked --quiet did not finish within 2s")
    assert took < 40, took
    assert _gone(int((tmp_path / "build.pid").read_text())), "the timed-out build outlived the gate"
    assert not any(c.startswith("run") for c in calls(tmp_path)), calls(tmp_path)


def test_a_server_that_moves_home_still_builds_with_the_real_toolchain(repo, tmp_path):
    """monitor names `HOME="$GOH_MCP_SCRATCH" cargo run`: under rustup that is a fresh HOME with no
    toolchain, so every run downloaded one (measured 2026-11-14) inside the bound. The gate pins
    CARGO_HOME and RUSTUP_HOME to the owner's, for the build and the server alike."""
    home = tmp_path / "home"
    (home / ".cargo").mkdir(parents=True)
    (home / ".rustup").mkdir()
    env = {"HOME": str(home)}
    for key in ("CARGO_HOME", "RUSTUP_HOME"):
        env[key] = ""
    r, _ = cargo_gate(
        repo,
        tmp_path,
        server_line(tmp_path, "moved"),
        timeout=20,
        build_s=0,
        env=env,
        prefix='HOME="$GOH_MCP_SCRATCH" ',
    )
    green(r)
    seen = (tmp_path / "env").read_text(encoding="utf-8").splitlines()
    want = f"CARGO_HOME={home / '.cargo'} RUSTUP_HOME={home / '.rustup'}"
    assert seen and all(s == want for s in seen), seen


TOKEN_POOL = (
    'POOL_STATE_DIR="$GOH_MCP_SCRATCH" cargo {} --release --locked --quiet'
    " --manifest-path rs/Cargo.toml"
)
DIVOOM = 'env -u A -u B S="$GOH_MCP_SCRATCH/s.sock" cargo {} --quiet --locked -p d --bin d'


@pytest.mark.parametrize(
    ("command", "build"),
    [
        (TOKEN_POOL.format("run") + " -- mcp", TOKEN_POOL.format("build")),
        (DIVOOM.format("run") + " -- mcp", DIVOOM.format("build")),
        ("cargo +nightly run --bin x", "cargo +nightly build --bin x"),
        ("cd rs && cargo run -- mcp", "cd rs && cargo build"),
        ("python3 srv.py", None),
        ("cargo run --bin x > log -- mcp", None),
        ("cargo run --bin a -- x; cargo run --bin b", None),
        ("mycargo run -- x", None),
    ],
)
def test_the_build_is_derived_from_a_cargo_run_and_only_from_one(command, build):
    assert mw.build_command(command) == build
