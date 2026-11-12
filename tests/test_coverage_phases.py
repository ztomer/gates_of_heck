"""coverage_gate.sh --lang rust streams its PHASES: a killed step names where its time went.

Hosted run 43320855869 (goh v0.107.1) was killed at GOH_STEP_TIMEOUT 1800 s in the coverage step,
and its log held the mode's three intro lines and nothing else: each part's output went to that
part's own log, read only when the part failed. Every phase now says when it started and what it
took, on the gate's stdout as it runs, and a stop signal names the phase it landed in
(GOH.COVPHASES). The fake `cargo` makes every phase instant except the one a test makes slow.
"""

from __future__ import annotations

import re
import stat
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env, llvm_cov_shim
from cov_fakes import SHOW_ENV_FLOW

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"
BOUNDED = REPO_ROOT / "lib" / "bounded_run.py"

FAKE_CARGO = (
    """\
#!/bin/bash
[ -n "${FAKE_CALLS:-}" ] && echo "$*" >> "$FAKE_CALLS"
case "$1" in
  metadata)
    echo '{"packages":[{"name":"ph","targets":[{"name":"ph","kind":["lib"]}]}]}'
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
        "workspace: cargo test",
        "workspace: report",
        "external: instrumented build",
        "external: true",
        "external: report",
        "merge + floor",
    ]
    started = re.findall(r"phase '([^']+)' started \d\d:\d\d:\d\dZ", out)
    assert started == phases, out
    ended = re.findall(r"✓ phase '([^']+)': \d+ s", out)
    assert ended == phases, out


def test_a_step_killed_on_its_ceiling_names_the_phase_it_was_in(tmp_path: Path) -> None:
    """A fake external suite sleeping past a 3 s ceiling: the kill leaves that phase named."""
    proj, env = _setup(tmp_path)
    marker = "suite-output-before-the-stall"
    cmd = [
        sys.executable,
        "-S",
        str(BOUNDED),
        "--timeout",
        "3",
        "--grace",
        "5",
        "--label",
        "coverage",
        "--",
        *_gate(proj, f"echo {marker}; sleep 60"),
    ]
    r = subprocess.run(
        cmd,
        cwd=proj,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    out = r.stdout + r.stderr
    assert r.returncode == 124, out
    # the phases before the stall ended; the stalled one started and is named by the kill
    assert "✓ phase 'external: instrumented build'" in out, out
    assert f"phase 'external: echo {marker}; sleep 60' started" in out, out
    assert re.search(
        rf"stopped by SIGTERM in phase 'external: echo {marker}; sleep 60' "
        r"after \d+ s",
        out,
    ), out
    assert marker in out, out  # the tail of the stalled phase's own log


def test_the_external_suite_drives_an_optimized_instrumented_build(tmp_path: Path) -> None:
    """The plain suite drives a RELEASE goh; under coverage it drove an unoptimized one, which
    measured 5-20x slower a call (an `emoji` scan 70 ms release, 1,439 ms debug-instrumented,
    271 ms instrumented at opt-level 2; 2026-11-12, GOH.COVPHASES). The external part's build is
    optimized; the workspace's cargo test keeps the debug profile it always measured."""
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
    tests = [c for c in lines if c.startswith("test ")]
    assert tests and not [c for c in tests if "opt-level" in c], tests
