"""Every admitted gate runs on its SHARE of the cores, never the machine's (GOH.POOLSHARE).

2026-11-17 ~20:40Z the load reached 213-237 on 16 cores: five gates and two land pre-steps ran at
once, EACH sizing its workers to all 16 cores. Admission bounds how many gates run; these pin how
wide each one runs -- cores / the gates admitted beside it, itself included, in every runner it
starts -- and refuse a new machine-sized pool in any gate script goh owns or vends.

The cores are pinned through GOH_GATE_CORES (16), so the answer is the same on a 3-core runner.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import worker_share

RUNNERS = (
    "GOH_GATE_WORKERS",
    "PYTEST_XDIST_AUTO_NUM_WORKERS",
    "CARGO_BUILD_JOBS",
    "RUST_TEST_THREADS",
    "NEXTEST_TEST_THREADS",
)
SHOW = "; ".join(f'echo "{k}=${{{k}:-unset}}"' for k in RUNNERS)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in hermetic_env().items() if k not in RUNNERS}
    env.update(
        GOH_DIR=str(REPO_ROOT),
        NO_COLOR="1",
        GOH_GATE_CORES="16",
        GOH_GATE_SLOTS="2",
        GOH_GATE_SLOT_DIR=str(tmp_path / "slots"),
        **extra,
    )
    (tmp_path / "slots").mkdir(exist_ok=True)
    return env


def _hold(tmp_path: Path, name: str):
    """Another gate holding slot `name` (an flock on another open file, as a holder's)."""
    f = open(tmp_path / "slots" / name, "w")  # noqa: SIM115  -- held for the test's length
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def _vars(out: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in out.splitlines() if line.split("=", 1)[0] in RUNNERS)


def _shell_gate(tmp_path: Path, body: str = SHOW, **extra: str) -> subprocess.CompletedProcess[str]:
    script = (
        f'set -euo pipefail; GOH="{REPO_ROOT}"; . "$GOH/tui/lib.sh"; . "$GOH/lib/gate_slot.sh"\n'
        f"trap gate_slot_release EXIT\ngate_slot_acquire probe\n{body}\n"
    )
    return subprocess.run(
        ["bash", "-c", script],
        env=_env(tmp_path, **extra),
        capture_output=True,
        text=True,
        timeout=patience(60),
        check=False,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.parametrize("beside", ["slot-0", "short-0"])
def test_a_shell_gate_admitted_beside_one_other_gets_half_the_cores_in_every_runner(
    tmp_path, beside
):
    """Either lane's holder is a gate beside this one: a commit gate runs a suite too."""
    (tmp_path / "slots").mkdir()
    other = _hold(tmp_path, beside)
    try:
        r = _shell_gate(tmp_path)
    finally:
        other.close()
    assert r.returncode == 0, r.stdout + r.stderr
    assert _vars(r.stdout) == dict.fromkeys(RUNNERS, "8"), r.stdout + r.stderr
    assert "workers: 8" in r.stdout + r.stderr


def test_a_shell_gate_admitted_alone_gets_the_machine_and_never_an_inherited_share(tmp_path):
    r = _shell_gate(tmp_path, GOH_GATE_WORKERS="3")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _vars(r.stdout) == dict.fromkeys(RUNNERS, "16"), r.stdout


def test_a_runner_a_repo_already_narrowed_is_never_widened(tmp_path):
    """RUST_TEST_THREADS=1 says the repo's tests cannot overlap: the share must not undo it."""
    r = _shell_gate(tmp_path, RUST_TEST_THREADS="1")
    assert _vars(r.stdout)["RUST_TEST_THREADS"] == "1", r.stdout
    assert _vars(r.stdout)["CARGO_BUILD_JOBS"] == "16", r.stdout


def test_pytest_xdist_itself_sizes_auto_to_the_share(tmp_path):
    """The runner, not only the variable: xdist's own `-n auto` count under the admission."""
    pytest.importorskip("xdist")
    (tmp_path / "slots").mkdir()
    other = _hold(tmp_path, "slot-1")
    ask = (
        f'"{sys.executable}" -c "import types, xdist.plugin as p; o = types.SimpleNamespace('
        "numprocesses='auto'); print(p.pytest_xdist_auto_num_workers(types.SimpleNamespace("
        'option=o)))"'
    )
    try:
        r = _shell_gate(tmp_path, body=ask)
    finally:
        other.close()
    assert r.stdout.strip().splitlines()[-1] == "8", r.stdout + r.stderr


def test_a_python_admitted_gate_gets_half_the_cores_beside_one_other(tmp_path):
    """tools/load.py's admission (a land's gate) exports the same share in-process."""
    (tmp_path / "slots").mkdir()
    other = _hold(tmp_path, "slot-0")
    code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'lib')!r}); import os, admission\n"
        "got = admission.admit_here('probe', max_wait=5)\n"
        f"print(got.state); [print(k + '=' + os.environ.get(k, 'unset')) for k in {RUNNERS!r}]"
    )
    try:
        r = subprocess.run(
            [sys.executable, "-c", code],
            env=_env(tmp_path),
            capture_output=True,
            text=True,
            timeout=patience(60),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    finally:
        other.close()
    assert r.stdout.startswith("taken"), r.stdout + r.stderr
    assert _vars(r.stdout) == dict.fromkeys(RUNNERS, "8"), r.stdout + r.stderr


def _share(tmp_path: Path, *args: str, **extra: str) -> str:
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "worker_share.py"), *args],
        env=_env(tmp_path, **extra),
        capture_output=True,
        text=True,
        timeout=patience(60),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_a_run_outside_any_slot_counts_itself_beside_the_admitted_gates(tmp_path):
    """A land's pre-step runs tools/pytest.sh under no slot: one more gate, never the machine."""
    assert _share(tmp_path, "share") == "16"
    (tmp_path / "slots").mkdir(exist_ok=True)
    held = [_hold(tmp_path, n) for n in ("slot-0", "short-0", "short-1")]
    try:
        assert _share(tmp_path, "share") == "4"
    finally:
        for f in held:
            f.close()


def test_steps_run_at_once_split_the_gates_share(tmp_path):
    assert _share(tmp_path, "share", "--split", "3", GOH_GATE_WORKERS="8") == "2"
    assert _share(tmp_path, "share", "--split", "16", GOH_GATE_WORKERS="8") == "1"


def test_local_ci_gives_each_of_two_steps_at_once_half_the_gates_share(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".gatesrc").write_text(
        f"GOH_CI_STEPS='echo $GOH_GATE_WORKERS/$CARGO_BUILD_JOBS >{tmp_path}/a"
        f":echo $GOH_GATE_WORKERS/$RUST_TEST_THREADS >{tmp_path}/b'\n"
        "GOH_CI_JOBS=2\n"
    )
    (tmp_path / "slots").mkdir()
    other = _hold(tmp_path, "slot-0")
    try:
        r = subprocess.run(
            ["bash", str(REPO_ROOT / "gates" / "local_ci.sh")],
            cwd=repo,
            env=_env(tmp_path),
            capture_output=True,
            text=True,
            timeout=patience(120),
            check=False,
            stdin=subprocess.DEVNULL,
        )
    finally:
        other.close()
    assert r.returncode == 0, r.stdout + r.stderr
    got = [(tmp_path / n).read_text().strip() for n in ("a", "b")]
    assert got == ["4/4", "4/4"], got


def test_the_release_probe_runs_pytest_on_the_share(monkeypatch):
    import release_gate

    monkeypatch.setenv("GOH_GATE_WORKERS", "5")
    assert " -n 5 " in release_gate.runner()


def test_the_coverage_engines_hand_swift_and_xcodebuild_the_share(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    import coverage_engines

    monkeypatch.setenv("GOH_GATE_WORKERS", "8")
    assert coverage_engines.workers() == ["8"]
    seen: list[list[str]] = []

    def fake_run(argv, **_kw):
        seen.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr(coverage_engines, "precondition", lambda *_a: None)
    monkeypatch.setattr(coverage_engines.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        coverage_engines.run_spm(None)
    assert "--jobs=8" in seen[-1], seen
    args = type("A", (), {"scheme": "App", "destination": "platform=macOS", "dd": "dd"})()
    with pytest.raises(SystemExit):
        coverage_engines.run_xcodebuild(args)
    got = seen[-1]
    assert got[got.index("-jobs") + 1] == "8", got
    assert got[got.index("-maximum-parallel-testing-workers") + 1] == "8", got


# THE RATCHET (GOH.POOLSHARE): a gate script never sizes a pool to the MACHINE. tests/
# test_suite_drift.py holds `cpu_count()`; this holds the rest of the class, in every tracked
# script goh runs or vends (gates/, lib/, tools/, checks/, hooks/, scripts/, the installers).
MACHINE_SIZED = worker_share.MACHINE_SIZED  # the one definition (checks/check_worker_share.py)
MACHINE_SIZED_SITES = {
    # Not a pool: the CPU-seconds ceiling of one bounded step (limit x cores), the wrapper's
    # backstop for a spinning step, not a count of workers.
    "gates/_common.sh": 1,
    "gates/local_ci.sh": 1,
    # Not a pool: the sampler's own core count, the denominator of a load reading.
    "lib/load_sources.py": 1,
    # Not a pool: THE count the share divides (GOH_GATE_CORES its seam), and MACHINE_SIZED's own
    # spelling of coreutils' count.
    "lib/worker_share.py": 2,
    # Not a run: the consumer check's planted probe files and the doc naming the fix.
    "checks/check_worker_share.py": 6,
    # Not a run: a flag a scanned command may carry, named so the scan reads past it.
    "lib/secret_render.py": 1,
}
SCRIPTS = ("*.sh", "*.py", "*.bash", "Makefile", "*.mk", "*.toml")


def _machine_sized() -> Counter[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *SCRIPTS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split()
    found: Counter[str] = Counter()
    for rel in out:
        if rel.startswith(("tests/", "docs/")):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line.lstrip().startswith("#") and MACHINE_SIZED.search(line):
                found[rel] += 1
    return found


@pytest.mark.load_sensitive
def test_no_new_machine_sized_pool_in_a_gate_script():
    found = _machine_sized()
    over = {f: n for f, n in found.items() if n > MACHINE_SIZED_SITES.get(f, 0)}
    stale = {f: n for f, n in MACHINE_SIZED_SITES.items() if found.get(f, 0) < n}
    assert not over, f"a new machine-sized pool (size it from goh_workers / worker_share): {over}"
    assert not stale, f"ratchet above its count -- lower or delete the entry: {stale}"


@pytest.mark.parametrize(
    "line",
    [
        "pytest -n auto",
        "python -m pytest -n=logical -q",
        "swift test --parallel",
        "make -j",
        "make -j && x",
        "cargo build -j  ",
        'cmake --build "$B" --target x -j',
        'n="$(nproc)"',
        "sysctl -n hw.ncpu",
        "getconf _NPROCESSORS_ONLN",
    ],
)
def test_the_ratchet_sees_every_shape_of_a_machine_sized_pool(line):
    assert MACHINE_SIZED.search(line), line


@pytest.mark.parametrize(
    "line",
    [
        'make -j"$(goh_workers)"',
        "make -j 4",
        "make -j8",
        'swift test --parallel --num-workers "$W"',
        "--jobs N",
        "pytest -n 8",
        "--parallel-testing-enabled",
        "nproc_x",
        "footprint -j 42",
        "READ = ['footprint', '-j', str(pid)]",
    ],
)
def test_the_ratchet_passes_a_bounded_pool(line):
    assert not MACHINE_SIZED.search(line), line


def test_divide_never_gives_zero_workers():
    assert worker_share.divide(16, 2) == 8
    assert worker_share.divide(16, 0) == 16
    assert worker_share.divide(3, 5) == 1


@pytest.mark.parametrize("mode", ["xcode", "spm"])
def test_the_swift_layer_hands_swift_and_xcodebuild_the_share(tmp_path, mode):
    """swift and xcodebuild read no variable: the layer that starts them passes the share."""
    from test_swift_gate_project_selection import _mk_repo

    repo, bin_ = _mk_repo(tmp_path, ["App.xcodeproj"])
    if mode == "spm":
        (repo / ".gatesrc").write_text("GOH_SWIFT_MODE=spm\n")
        (repo / "Package.swift").write_text("// swift-tools-version:6.0\n")
    log = tmp_path / "argv"
    for name in ("swift", "xcodebuild"):
        stub = bin_ / name
        stub.write_text(f'#!/bin/bash\necho "{name} $*" >> "{log}"\nexit 0\n')
    env = hermetic_env(
        PATH=f"{bin_}:{os.environ['PATH']}", GOH_GATE_WORKERS="8", GOH_SWIFT_COLD="0"
    )
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "swift_gate.sh"), str(repo)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=patience(120),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    runs = [line for line in log.read_text().splitlines() if " test" in line or " build" in line]
    assert runs, r.stdout + r.stderr
    for line in runs:
        if line.startswith("xcodebuild"):
            assert "-jobs 8 -maximum-parallel-testing-workers 8" in line, line
        elif "--show-" not in line:
            assert "--jobs 8" in line, line


def test_the_coverage_runner_splits_the_share_among_the_binaries_it_runs_at_once():
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    import _cov_run_tests as cov

    runs = [cov._Run(f"b{i}", ["x"], ".", {}) for i in range(3)]
    runs[2].env["RUST_TEST_THREADS"] = "1"  # the repo's own: its tests cannot overlap
    assert cov.split_share(6, runs, "8") == 6
    assert [r.env["RUST_TEST_THREADS"] for r in runs] == ["1", "1", "1"]
    runs = [cov._Run("b", ["x"], ".", {}) for _ in range(2)]
    assert cov.split_share(2, runs, "16") == 2
    assert [r.env["RUST_TEST_THREADS"] for r in runs] == ["8", "8"]
    assert cov.split_share(6, runs, "") == 6  # outside any gate: unchanged


@pytest.mark.load_sensitive
def test_the_structural_checks_pool_runs_on_the_share(monkeypatch):
    """checks/_ordered_pool.py ran cpu_count threads inside every gate; now the gate's share."""
    import threading

    sys.path.insert(0, str(REPO_ROOT / "checks"))
    import _ordered_pool

    monkeypatch.setenv("GOH_GATE_WORKERS", "2")
    live, peak, lock = [0], [0], threading.Lock()

    def one(_item):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)  # long enough that a machine-wide pool overlaps more than two
        with lock:
            live[0] -= 1

    _ordered_pool.in_order(one, range(6))
    assert peak[0] <= 2, peak
