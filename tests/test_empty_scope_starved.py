"""A starved gate is NO VERDICT, never an empty tree turned red (GOH.CANARYLOAD, GOH.CANARYFLAKE).

The release canary refused lands on "Adv360-Pro-ZMK: gates refuse to pass over an empty tree --
fix the findings" (2026-11-14, load ~80, three goh lanes gating at once) and on CadGoose
(2026-11-12, load 80-110), trees that pass on the re-run. The empty-scope sweep gave each gate 90
seconds of WALL time: Adv360's check_probe_bite needs ~10 s at load 45 (nine nested probes), so at
load 150 beside four canary jobs and a coverage run it is starved past the bound, the sweep said
"gave NO verdict" and exited 1, and the canary read that red step as a finding.

Two fixes, each pinned here, each seen red first:
  * the sweep's bound is the house's (DD-770): CPU the gate's group spent, a stall, and a wall
    BACKSTOP -- a starved gate (runnable, spending no CPU) gets its verdict inside the backstop;
  * past the backstop it is the house's `TIMED OUT after`, exit 124, and the canary reads such a
    step as a run that gave no verdict -- a refusal that says so -- never as `turned_red`.

Starvation is made deterministic through the sampler seam (`pgid_cpu.pgid_cpu_and_runnable`):
"runnable, no CPU spent" is exactly what a starved process looks like to it.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "checks"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import check_empty_scope as ces
import pgid_cpu
import source_closure

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
LABEL = "gates refuse to pass over an empty tree"


@pytest.fixture
def starved(monkeypatch):
    """Every gate looks starved to the sweep: runnable, and spending no CPU."""
    monkeypatch.setattr(pgid_cpu, "pgid_cpu_and_runnable", lambda _pgid: (0.0, 1))


def _repo(tmp_path: Path, gates: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    fast_init(root)
    (root / "tools").mkdir()
    for name, body in gates.items():
        (root / "tools" / name).write_text(body)
    return root


def _sweep(root: Path, timeout: int) -> tuple[dict, dict]:
    gates = ces.gate_dir(str(root))
    with contextlib.ExitStack() as stack:
        import tempfile

        work = stack.enter_context(tempfile.TemporaryDirectory(prefix="goh-starved."))
        skeleton = ces.build_skeleton(str(root), gates, os.path.join(work, "skeleton"))
        return ces.sweep(skeleton, "tools", ces.gate_names(gates), timeout=timeout)


@pytest.mark.load_sensitive
def test_a_starved_gate_inside_the_backstop_gets_its_verdict(tmp_path, starved):
    # Two seconds of wall against a nominal bound of one: the wall alone said "no verdict".
    root = _repo(tmp_path, {"check_slow.py": "import time, sys\ntime.sleep(2)\nsys.exit(1)\n"})
    blind, unrunnable = _sweep(root, timeout=1)
    assert unrunnable == {} and blind == {}, (blind, unrunnable)


def _main(root: Path, monkeypatch) -> tuple[int, str]:
    monkeypatch.setattr(ces, "TIMEOUT", 1)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        rc = ces.main(["--root", str(root)])
    return rc, buf.getvalue()


@pytest.mark.load_sensitive
def test_a_gate_starved_past_the_backstop_is_timed_out_not_red(tmp_path, starved, monkeypatch):
    root = _repo(tmp_path, {"check_hung.py": "import time\ntime.sleep(60)\n"})
    (root / "tools" / ces.ALLOW_FILE).write_text(
        json.dumps({"legitimate": {"check_hung.py": "slow, and correct"}})
    )
    rc, out = _main(root, monkeypatch)
    assert rc == 124, out  # GNU timeout's code: the house's "no verdict", not 1's "red"
    assert "TIMED OUT after 4s: check_hung.py" in out, out  # the backstop, four times the bound
    assert "starved" in out, out
    assert "delete its excuse" not in out and "PASSED over" not in out, out


@pytest.mark.load_sensitive
def test_a_real_finding_beside_a_timeout_is_still_red(tmp_path, starved, monkeypatch):
    # The calibration: a blind gate next to a starved one keeps exit 1 -- the timeout hides nothing.
    root = _repo(
        tmp_path,
        {
            "check_hung.py": "import time\ntime.sleep(60)\n",
            "check_blind.py": "print('ok 0 files clean')\n",
        },
    )
    rc, out = _main(root, monkeypatch)
    assert rc == 1, out
    assert "check_blind.py PASSED over an empty tree" in out, out


def _canary_mod():
    spec = importlib.util.spec_from_file_location("release_canary_starved", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _structural(step_output: str) -> str:
    """`goh structural --full --keep-going` around one failing delegated step, as the native tier
    frames it (step_report.rs: begin, the tail of the step's output, then the failure line)."""
    return (
        "· no disallowed emoji (≤1800s)\n✓ no disallowed emoji\n"
        f"· {LABEL} (≤1800s)\n\n{step_output}\n\n"
        f"✗ structural: {LABEL} failed (command: python3 check_empty_scope.py)\n"
        "  → docs/map.md documents this gate\n✗ 1 structural step failed\n"
    )


def _starved_output(tmp_path, monkeypatch) -> str:
    root = _repo(tmp_path, {"check_hung.py": "import time\ntime.sleep(60)\n"})
    (root / "tools" / ces.ALLOW_FILE).write_text(json.dumps({"legitimate": {"check_hung.py": "x"}}))
    rc, out = _main(root, monkeypatch)
    assert rc == 124, out
    return out


@pytest.mark.load_sensitive
def test_the_canary_reads_a_starved_step_as_no_verdict(tmp_path, starved, monkeypatch):
    v = _canary_mod().parse(1, _structural(_starved_output(tmp_path, monkeypatch)))
    assert v.error is None, v.error
    assert LABEL not in v.red, v.red  # never "turned red -- fix the findings"
    assert "TIMED OUT after" in v.timed_out[LABEL], v.timed_out


def test_the_canary_still_reads_a_real_empty_tree_finding_as_red():
    out = "✗ check_blind.py PASSED over an empty tree: 'ok 0 files clean'\n"
    v = _canary_mod().parse(1, _structural(out + "TIMED OUT after 4s: check_hung.py\n"))
    assert LABEL in v.red and LABEL not in v.timed_out, (v.red, v.timed_out)


def _bin(at: Path, text: str, rc: int) -> Path:
    (at.parent / f"{at.name}.out").write_text(text)
    at.write_text(f"#!/usr/bin/env bash\ncat '{at.parent / (at.name + '.out')}'\nexit {rc}\n")
    at.chmod(0o755)
    return at


@pytest.mark.load_sensitive
def test_a_starved_consumer_refuses_the_land_as_a_timeout(tmp_path, starved, monkeypatch):
    """End to end: the run refuses (fail closed) and SAYS it was starved; no consumer turned red."""
    step_out = _starved_output(tmp_path / "s", monkeypatch)
    consumer = tmp_path / "consumers" / "adv"
    fast_init(consumer)
    write_gatesrc(consumer, "GOH_MAX_LINES=500\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (
        ["add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"],
    ):
        subprocess.run(
            ["git", *args],
            cwd=consumer,
            check=True,
            capture_output=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{consumer}\n")
    green = f"· {LABEL} (≤1800s)\n✓ {LABEL}\n✓ all structural gates passed\n"
    base = _bin(tmp_path / "base-goh", green, 0)
    head = _bin(tmp_path / "head-goh", _structural(step_out), 1)
    (tmp_path / "root").mkdir()
    argv = [sys.executable, str(SCRIPT), "--root", str(tmp_path / "root"), "--state", str(state)]
    argv += ["--base-bin", str(base), "--head-bin", str(head), "--goh", str(REPO_ROOT)]
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    rec = json.loads(state.read_text())["release_canary"]
    assert rec["turned_red"] == {}, rec
    assert any("TIMED OUT after" in e and "no verdict" in e for e in rec["errors"]), rec
    assert "fix the findings" not in out, out


def test_the_skeleton_carries_the_bash_guard_a_shell_gate_sources(tmp_path):
    """GOH.BASH5SILENT: every goh shell gate sources `../lib/bash5.sh || exit`. The skeleton copied
    the gate directory alone, so a shell gate died on the missing guard and the sweep read that as
    the gate failing on an empty tree (check_no_screen_linkage.sh's excuse called stale). The guard
    comes along, ignored like the gate directory, so the sweep measures the gate, not its guard."""
    root = tmp_path / "repo"
    (root / "checks").mkdir(parents=True)
    source_closure.copy_closure(root, "lib/bash5.sh")
    gate = root / "checks" / "check_args.sh"
    gate.write_text(
        '#!/usr/bin/env bash\n. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" || exit; '
        'goh_bash5 "$@"\necho "usage: check_args.sh FILE" >&2\nexit 2\n'
    )
    gate.chmod(0o755)
    fast_init(root)
    skeleton = ces.build_skeleton(str(root), str(root / "checks"), str(tmp_path / "skeleton"))
    r = subprocess.run(
        ["bash", os.path.join(skeleton, "checks", "check_args.sh")],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 2 and "usage:" in r.stderr, r.stderr
    listed = subprocess.run(
        ["git", "-C", skeleton, "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout  # fmt: skip
    assert "bash5.sh" not in listed, listed


def test_the_skeleton_carries_what_a_shell_gate_sources_read_from_its_source(tmp_path):
    """GOH.CLASS.HARNESS: the guard was a hand list (`GUARD = "lib/bash5.sh"`), right until a gate
    sources anything else. The skeleton reads each gate's own `.` lines (lib/source_closure.py), so
    a helper two hops away comes along, and stays ignored like the gate directory."""
    root = tmp_path / "repo"
    (root / "checks").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "lib" / "helper.sh").write_text('. "${BASH_SOURCE[0]%/*}/deeper.sh"\n')
    (root / "lib" / "deeper.sh").write_text("say() { echo usage: check_args.sh FILE >&2; }\n")
    gate = root / "checks" / "check_args.sh"
    gate.write_text(
        '#!/usr/bin/env bash\nHERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        '. "$HERE/../lib/helper.sh" || exit 1\nsay\nexit 2\n'
    )
    gate.chmod(0o755)
    fast_init(root)
    skeleton = ces.build_skeleton(str(root), str(root / "checks"), str(tmp_path / "skeleton"))
    r = subprocess.run(
        ["bash", os.path.join(skeleton, "checks", "check_args.sh")],
        capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert r.returncode == 2 and "usage:" in r.stderr, r.stderr
    listed = subprocess.run(
        ["git", "-C", skeleton, "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout  # fmt: skip
    assert "helper.sh" not in listed and "deeper.sh" not in listed, listed
