"""checks/check_mcp_wire.py: a `cargo run` server is BUILT first, under its own bound.

GOH.MCPWIRELOAD. Split from tests/test_check_mcp_wire.py at its 500-line cap; the planted servers
and the verdict helpers are that file's.
"""

import os
import signal
import subprocess
import sys
import threading
import time

import pid_owner  # lib/, on the path since conftest imported load_kit
import pytest
from conftest import hermetic_env, write
from test_check_mcp_wire import CHECK, _gone, green, mw, red, server_line
from timing_bounds import patience

from lib.bounded_run import _descendants

# --- the build is not the answer (GOH.MCPWIRELOAD) -----------------------------------------------
# token-pool 751531b's push failed "no answer to server/discover within 300s" at load ~120: its
# server is `cargo run --release`, and the build ran inside the bound meant for the wire answer. A
# planted `cargo` on PATH stands in for the real one: `build` takes FAKE_CARGO_BUILD_S and leaves a
# stamp; `run` builds first when the stamp is missing (as cargo does), then execs what follows `--`.
# `nap` is a build's TIME, not one `sleep`: `sleep N; : > built` read a sleep that a stray signal
# ended as a finished build, and the gate ran the server (GOH.MCPWIREBUILDSKIP).
FAKE_CARGO = r"""#!/bin/sh
d="$FAKE_CARGO_DIR"
nap() {
  start=$(date +%s) || exit 70
  sleep "$1" && return 0
  while :; do
    now=$(date +%s) || exit 70
    [ "$now" -ge "$((start + $1))" ] && return 0
    sleep "$((start + $1 - now))"
  done
}
echo "$*" >> "$d/calls"
echo "CARGO_HOME=${CARGO_HOME:-} RUSTUP_HOME=${RUSTUP_HOME:-}" >> "$d/env"
case "$1" in
build)
  if [ -n "${FAKE_CARGO_FAIL:-}" ]; then
    echo "error[E0425]: planted build failure" >&2; exit 101
  fi
  echo $$ > "$d/build.pid"
  nap "$FAKE_CARGO_BUILD_S"; : > "$d/built"; exit 0 ;;
run)
  [ -f "$d/built" ] || { nap "$FAKE_CARGO_BUILD_S"; : > "$d/built"; }
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
    run_env = hermetic_env(**(env or {}))
    run_env["PATH"] = f"{bin_dir}{os.pathsep}{run_env['PATH']}"
    run_env["FAKE_CARGO_DIR"] = str(tmp_path)
    run_env["FAKE_CARGO_BUILD_S"] = str(build_s)
    return subprocess.run(
        [sys.executable, str(CHECK), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=run_env,
        timeout=180,
    )


def calls(tmp_path):
    p = tmp_path / "calls"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_a_slow_build_and_a_fast_server_is_green_the_build_is_not_timed_as_the_answer(
    repo, tmp_path
):
    """The build is longer than the whole wire bound; the server answers at once.

    The bound is 6 s on a quiet box and stretched by the load (GOH.COVPHASESTALL): a fixed 6 s was
    the box's answer time at load ~114, not the server's. The build stays 4 s past it."""
    wire = int(patience(6))
    r = cargo_gate(repo, tmp_path, server_line(tmp_path, "built"), timeout=wire, build_s=wire + 4)
    green(r)
    assert calls(tmp_path)[0] == "build --release --locked --quiet", calls(tmp_path)


@pytest.mark.load_sensitive
def test_a_built_server_that_never_answers_is_still_red_as_no_answer(repo, tmp_path):
    r = cargo_gate(repo, tmp_path, server_line(tmp_path, "mute", hang=True), timeout=2, build_s=0)
    out = red(r, "wire: no answer to server/discover within 2s")
    assert "build:" not in out, out


@pytest.mark.load_sensitive
def test_a_failed_build_says_build_with_its_output_and_starts_no_server(repo, tmp_path):
    r = cargo_gate(
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


@pytest.mark.load_sensitive
def test_a_build_past_its_own_bound_says_build_and_is_reaped(repo, tmp_path):
    r = cargo_gate(
        repo,
        tmp_path,
        server_line(tmp_path, "slow"),
        timeout=20,
        build_s=60,
        extra="GOH_MCP_BUILD_TIMEOUT=2\n",
    )
    red(r, "build: cargo build --release --locked --quiet did not finish within 2s")
    assert _gone(int((tmp_path / "build.pid").read_text())), "the timed-out build outlived the gate"
    assert not any(c.startswith("run") for c in calls(tmp_path)), calls(tmp_path)


def _signal_the_build_sleep(tmp_path, sig, deadline_s):
    """Send `sig` to the fake build's `sleep` once it runs: a stray signal of
    the kind another process on a loaded box sends. True when one was signalled."""
    pidfile = tmp_path / "build.pid"
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if pidfile.exists() and pidfile.read_text().strip():
            for kid in _descendants(int(pidfile.read_text())):
                held = pid_owner.stamp(kid)  # before the name is read, so the name is its
                comm = subprocess.run(
                    ["ps", "-o", "comm=", "-p", str(kid)],
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                ).stdout.strip()
                if os.path.basename(comm) == "sleep" and pid_owner.signal(held, sig):
                    return True
        time.sleep(0.05)
    return False


@pytest.mark.load_sensitive
def test_a_build_whose_sleep_is_signalled_still_takes_its_whole_time(repo, tmp_path):
    """GOH.MCPWIREBUILDSKIP: in GOH.LANDMISS1119's land the 60 s fake build ended in under 2 s and
    the gate ran the server (exit 0). Its kept tmp_path held `calls` = build then run, `build.pid`
    at 06:30:32 and `built` at 06:30:33: the build RAN and its `sleep` ended early. The fake read
    `sleep N; : > built; exit 0`, so a `sleep` ended by any stray signal was a finished build,
    exit 0. A fake build lasts its whole time whatever happens to one `sleep`."""
    done = []
    t = threading.Thread(
        target=lambda: done.append(_signal_the_build_sleep(tmp_path, signal.SIGTERM, 20)),
        daemon=True,
    )
    t.start()
    r = cargo_gate(
        repo,
        tmp_path,
        server_line(tmp_path, "stray"),
        timeout=int(patience(20)),
        build_s=60,
        extra="GOH_MCP_BUILD_TIMEOUT=3\n",
    )
    t.join(patience(25))
    assert done == [True], "the planted signal never reached the build's sleep"
    red(r, "build: cargo build --release --locked --quiet did not finish within 3s")
    assert not any(c.startswith("run") for c in calls(tmp_path)), calls(tmp_path)


@pytest.mark.load_sensitive
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
    r = cargo_gate(
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
