"""A land's `cargo test (plain)` runs BESIDE its external suite (GOH.RUSTOVERLAP).

They ran in turn, cargo first (43 s p50 of a land's gate), though neither reads what the other
writes. Now both start at once, each on its named part of the gate's share, and the first red stops
the other whole. The real gates/_rust_coverage.sh and goh_step run here under a harness with two
seams (the proven group, which runs; the plain build's directory); cargo is a script.

Concurrency is proven by a RENDEZVOUS, not a stopwatch: each side waits for the other's mark, so a
serial run deadlocks into the waiting side's red and a concurrent one passes on any load.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from orphan_kit import adopt, wait_for_file
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner

# The gate's own spelling around the function: its scaffold, its trap, its done.
HARNESS = """
set -euo pipefail
GOH_PY="${GOH_PY:-python3}"
HERE="%(root)s/gates"
. "$HERE/_common.sh"
. "$HERE/../lib/worker_share.sh"
. "$HERE/../lib/timings_hold.sh"
cargo_dir="$PWD"
GOH_COV_RUST_EXTERNAL='bash ext.sh'
goh_init rust
. "$HERE/_rust_coverage.sh"
rust_cov_plain_bins() { echo /plain; }
rust_proven_group() { "$3"; }
rust_coverage_plain_only
goh_done
"""
WAIT_S = 60  # a side's wait for the other's mark
# Shell: wait (bounded) for cargo's share file to go, which the first part to end takes away.
GONE = "for _ in $(seq 1200); do [ -e $last ] || break; sleep 0.05; done"


class Leak:
    """A step that starts a child deaf to TERM under a shell that dies of one, and waits: only the
    step's own sweep (its whole session, KILL after the grace) ends that child. The test's end
    releases it (orphan_kit's bounded wait), so a red test leaves none."""

    def __init__(self, where: Path, name: str) -> None:
        self.pid, self.release = where / f"{name}.pid", where / f"{name}.release"
        says = (f". {REPO_ROOT}/lib/pid_owner.sh; goh_pid_stamp $BASHPID >{self.pid}.t;"
                f" mv {self.pid}.t {self.pid}")  # fmt: skip
        self.sh = f"( trap '' TERM; {says}; {wait_for_file(self.release)} ) & wait"

    def alive(self) -> bool:
        return pid_owner.alive_line(self.pid.read_text())


@pytest.fixture
def leak(tmp_path: Path):
    made: list[Leak] = []

    def make(name: str) -> Leak:
        made.append(Leak(tmp_path, name))
        return made[-1]

    yield make
    for one in made:
        one.release.touch()


@pytest.fixture
def gate(tmp_path: Path, goh: Path):
    """start(cargo, external) -> Popen of the harness; both bodies are shell text."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "run.sh").write_text(HARNESS % {"root": REPO_ROOT})

    def start(cargo: str, external: str) -> subprocess.Popen:
        fake = tmp_path / "bin" / "cargo"
        fake.write_text(f"#!/usr/bin/env bash\n{cargo}\n")
        fake.chmod(0o755)
        (tmp_path / "ext.sh").write_text(external + "\n")
        env = hermetic_env(drop_git=True, GOH_RESOLVED_BIN=str(goh), GOH_STEP_GRACE="1",
                           GOH_GATE_WORKERS="8", RUST_TEST_THREADS="8")  # fmt: skip
        env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{env['PATH']}"
        with (tmp_path / "out").open("w") as out:
            return subprocess.Popen(["bash", "run.sh"], cwd=tmp_path, env=env, stdout=out,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    start_new_session=True)  # fmt: skip

    return start


def _end(proc: subprocess.Popen, out: Path) -> tuple[int, str]:
    adopt(proc).wait(timeout=patience(60))
    return proc.returncode, out.read_text()


@pytest.mark.load_sensitive
def test_both_run_at_once_each_on_its_part_and_print_apart(gate, tmp_path: Path) -> None:
    """The suite ends first, and cargo then finds its share whole: its share file is read as the
    runners read it (lib/worker_share.py part_of)."""
    ext, up = tmp_path / "ext.mark", tmp_path / "cargo.mark"
    rc, out = _end(gate(
        "last=${GOH_SHARE_STEPS##*:};"
        f" echo $RUST_TEST_THREADS $(cat $last) >{tmp_path}/cargo.part;"
        f" touch {up}; {wait_for_file(ext, seconds=WAIT_S)}; {GONE};"
        f" [ ! -e $last ] && touch {tmp_path}/whole; echo '⚠ cargo said'",
        f"echo $GOH_GATE_WORKERS ${{GOH_SHARE_STEPS-none}} >{tmp_path}/ext.part; touch {ext};"
        f" {wait_for_file(up, seconds=WAIT_S)}; echo '⚠ suite said'",
    ), tmp_path / "out")  # fmt: skip
    assert rc == 0, out
    assert (tmp_path / "cargo.part").read_text() == "4 1/2\n"  # half of GOH_GATE_WORKERS=8
    assert (tmp_path / "ext.part").read_text() == "8 none\n"  # the gate's own
    assert (tmp_path / "whole").exists(), out  # the suite ended: cargo has the whole share
    order = [out.index(x) for x in (  # the suite's part ends first and is printed whole first
        "· external (plain)", "✓ external (plain)", "⚠ suite said",
        "· cargo test (plain)", "✓ cargo test (plain)", "⚠ cargo said")]  # fmt: skip
    assert order == sorted(order), out


@pytest.mark.load_sensitive
@pytest.mark.parametrize("red", ["cargo test (plain)", "external (plain)"])
def test_a_red_is_red_with_the_other_green(gate, tmp_path: Path, red: str) -> None:
    """The green one has ended (its part is printed) before the red one fails: neither hides it."""
    green = "✓ external (plain)" if red.startswith("cargo") else "✓ cargo test (plain)"
    fails = (f"n=0; while ! grep -q '^{green}' {tmp_path}/out && [ $n -lt 1200 ];"
             " do n=$((n + 1)); sleep 0.05; done; echo the red test; exit 3")  # fmt: skip
    rc, out = _end(
        gate(*((fails, "true") if red.startswith("cargo") else ("true", fails))), tmp_path / "out"
    )
    assert rc == 1, out
    assert green in out and "the red test" in out, out
    assert f"rust: {red}" in out and f"[rust] {red} is red" in out, out
    assert "stopped" not in out, out
    assert "all rust gates passed" not in out, out


@pytest.mark.load_sensitive
@pytest.mark.parametrize("red", ["cargo test (plain)", "external (plain)"])
def test_the_first_red_stops_the_other_and_leaves_no_child(
    gate, leak, tmp_path: Path, red: str
) -> None:
    hang = leak("hang")
    fails = f"{wait_for_file(hang.pid, seconds=WAIT_S)}; echo the red test; exit 3"
    bodies = (fails, hang.sh) if red.startswith("cargo") else (hang.sh, fails)
    rc, out = _end(gate(*bodies), tmp_path / "out")
    name = {"cargo test (plain)": "external (plain)"}.get(red, "cargo test (plain)")
    assert rc == 1, out
    assert f"{name}: stopped, no verdict -- {red} went red first" in out, out
    assert f"✗ rust: {red}" in out and "the red test" in out, out
    assert f"✗ rust: {name}" not in out, out  # the stopped one is no red
    assert not hang.alive(), out


@pytest.mark.load_sensitive
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_a_stopped_gate_stops_both(gate, leak, tmp_path: Path, sig: signal.Signals) -> None:
    """Background parts ignore INT from birth: the gate's own trap stops them."""
    one, two = leak("one"), leak("two")
    proc = gate(one.sh, two.sh)
    wait = GiveUp(60)
    while not (one.pid.exists() and two.pid.exists()) and not wait.passed():
        time.sleep(0.05)
    proc.send_signal(sig)
    rc, out = _end(proc, tmp_path / "out")
    assert rc == 128 + sig, out
    assert "were stopped with it" in out, out
    assert not one.alive() and not two.alive(), out
