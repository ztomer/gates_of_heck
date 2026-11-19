"""coverage_gate.sh --lang rust streams its PHASES: a killed step names where its time went.

Hosted run 43320855869 (goh v0.107.1) was killed at GOH_STEP_TIMEOUT 1800 s in the coverage step,
and its log held the mode's three intro lines and nothing else: each part's output went to that
part's own log, read only when the part failed. Every phase now says when it started and what it
took, on the gate's stdout as it runs, and a stop signal names the phase it landed in
(GOH.COVPHASES). The fake `cargo` makes every phase instant except the one a test makes slow.
"""

from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env, llvm_cov_shim
from cov_fakes import SHOW_ENV_FLOW
from timing_bounds import GiveUp, patience

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"

FAKE_CARGO = (
    """\
#!/bin/bash
[ -n "${FAKE_CALLS:-}" ] && echo "$*" >> "$FAKE_CALLS"
case "$1" in
  metadata)
    echo '{"packages":[{"name":"ph","manifest_path":"'"$PWD"'/Cargo.toml",
      "targets":[{"name":"ph","kind":["lib"]}]}]}'
    exit 0 ;;
esac
"""
    + SHOW_ENV_FLOW
    + """\
case "$1" in
  build)
    exe="$CARGO_LLVM_COV_TARGET_DIR/debug/ph"
    mkdir -p "${exe%/*}"; : >"$exe"; chmod +x "$exe"
    printf '{"reason":"compiler-artifact","filenames":["%s"],"executable":"%s"}\\n' "$exe" "$exe"
    exit 0 ;;
  llvm-cov)
    case "$2" in --version|clean) exit 0 ;; esac
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "--output-path" ] && out="$a"; prev="$a"; done
    printf 'SF:%s/src/lib.rs\\nDA:1,1\\nLF:1\\nLH:1\\nend_of_record\\n' "$PWD" > "$out"
    exit 0 ;;
esac
exit 0
"""
)


def _setup(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    shim = bin_ / "cargo"
    shim.write_text(FAKE_CARGO)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    llvm_cov_shim(bin_)
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "lib.rs").write_text("pub fn f() {}\n")
    env = hermetic_env(PATH=f"{bin_}:/usr/bin:/bin", GOH_COV_CACHE=str(tmp_path / "covcache"))
    return proj, env


def _gate(proj: Path, external: str) -> list[str]:
    return [
        "/bin/bash",
        str(COV_GATE),
        "--lang",
        "rust",
        "--floor",
        "0",
        "--external",
        external,
        str(proj),
    ]


def test_every_phase_streams_its_start_and_its_elapsed(tmp_path: Path) -> None:
    proj, env = _setup(tmp_path)
    r = subprocess.run(
        _gate(proj, "true"),
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    phases = [
        "workspace: instrumented build",
        "workspace: test binaries",
        "workspace: report",
        "external: instrumented build",
        "external: true",
        "external: report",
        "merge + floor",
        "diff coverage",
    ]
    started = re.findall(r"phase '([^']+)' started \d\d:\d\d:\d\dZ", out)
    assert started == phases, out
    ended = re.findall(r"✓ phase '([^']+)': \d+ s", out)
    assert ended == phases, out


@pytest.mark.load_sensitive
def test_a_step_killed_on_its_ceiling_names_the_phase_it_was_in(tmp_path: Path) -> None:
    """A fake external suite stalled in its phase: the stop signal leaves that phase named.

    The signal is the TEST's, sent when the stalled phase says it is running (GOH.COVPHASESTALL).
    Twice the ceiling that sent it was a wall-clock one (`--timeout 3`, then `--stall 3`), and at
    load 110-180 it expired in a prefix the box starved before the phase began: the test judged the
    machine, not the naming. Whether a ceiling expires an idle group is bounded_run's own clock,
    driven by a fake one in tests/test_stall_clock.py; here the stalled phase says "I am running"
    by touching a file, and only then does the test stop the step: no clock decides when."""
    proj, env = _setup(tmp_path)
    marker = "suite-output-before-the-stall"
    ready = tmp_path / "stalled-phase-is-running"
    phase = f"echo {marker}; touch {ready}; sleep 60"
    log = tmp_path / "gate.out"
    with log.open("wb") as sink:
        gate = subprocess.Popen(
            _gate(proj, phase),
            cwd=proj,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # its own group, as bounded_run gives a step
        )
        try:
            until = GiveUp(60)
            while not ready.exists():
                assert gate.poll() is None, log.read_text()
                assert not until.passed(), "the stalled phase never started: " + log.read_text()
                time.sleep(0.05)
            os.killpg(gate.pid, signal.SIGTERM)  # what bounded_run's sweep sends on its ceiling
            code = gate.wait(timeout=patience(60))
        finally:
            if gate.poll() is None:
                os.killpg(gate.pid, signal.SIGKILL)
                gate.wait()
    out = log.read_text()
    assert code == 143, out
    # the phases before the stall ended; the stalled one started and is named by the stop
    assert "✓ phase 'external: instrumented build'" in out, out
    assert f"phase 'external: {phase}' started" in out, out
    assert re.search(
        rf"stopped by SIGTERM in phase 'external: {re.escape(phase)}' after \d+ s", out
    ), out
    assert marker in out, out  # the tail of the stalled phase's own log


def test_the_external_suite_drives_an_optimized_instrumented_build(tmp_path: Path) -> None:
    """The plain suite drives a RELEASE goh; under coverage it drove an unoptimized one, which
    measured 5-20x slower a call (an `emoji` scan 70 ms release, 1,439 ms debug-instrumented,
    271 ms instrumented at opt-level 2; 2026-11-12, GOH.COVPHASES). The external part's build is
    optimized; the workspace's test binaries keep the debug profile it always measured.

    And REPRODUCIBLE: a test's stored profile is keyed on these bytes (lib/prof_store.py), and
    with local ThinLTO across codegen units five builds of one tree gave four sha256s
    (GOH.PROFSTOREEMPTY, 2026-11-18), so no land found the profiles the last one kept. Local
    ThinLTO off: one sha256 from every build of those inputs, across a commit."""
    proj, env = _setup(tmp_path)
    calls = tmp_path / "calls"
    env["FAKE_CALLS"] = str(calls)
    r = subprocess.run(
        _gate(proj, "true"),
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    lines = calls.read_text().splitlines()
    builds = [c for c in lines if c.startswith("build ")]
    assert len(builds) == 1 and "--config profile.dev.opt-level=2" in builds[0], lines
    assert '--config profile.dev.lto="off"' in builds[0], builds
    tests = [c for c in lines if c.startswith("test ")]
    assert tests and not [c for c in tests if "opt-level" in c], tests


def test_every_phase_is_a_timings_row_so_the_step_s_seconds_are_named(tmp_path: Path) -> None:
    """GOH.RELEASELANDGATE: v0.139.4's coverage step was 388 s and the rows under it named 4 s of
    it (lib/step_attribution.py); each phase it prints is a row of the step's profile too."""
    import json

    proj, env = _setup(tmp_path)
    timings = tmp_path / "t.jsonl"
    env |= {"GOH_TIMINGS": str(timings), "GOH_TIMINGS_PARENT": "coverage (floor 0%)"}
    r = subprocess.run(_gate(proj, "true"), cwd=proj, capture_output=True, text=True, env=env,
                       timeout=patience(120), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [json.loads(x) for x in timings.read_text().splitlines() if x.strip()]
    got = [x["label"] for x in rows if x["label"].startswith("coverage phase: ")]
    assert got[:2] == ["coverage phase: workspace: instrumented build",
                       "coverage phase: workspace: test binaries"], rows  # fmt: skip
    assert "coverage phase: diff coverage" in got, rows
    assert {x["parent"] for x in rows if x["label"] in got} == {"coverage (floor 0%)"}, rows
