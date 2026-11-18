"""tools/pytest.sh gives every run its own temp root, so concurrent suites cannot delete each other's.

pytest keeps the three newest `pytest-of-<user>/pytest-N` directories and deletes older ones when a
new session starts. Two sessions landing into this repo at once (2026-10-10) meant the newer run
pruned the older run's root mid-run: 2277 tests errored `FileNotFoundError` on `popen-gwN`, once an
autouse fixture made every test touch its temp root. A temp root of the run's own is never
pruned by another session: `gates/_pytest_root.sh` exports pytest's own `PYTEST_DEBUG_TEMPROOT`
override, private to the run. The stub `python3` below records the root the runner hands pytest.
"""

import os
import re
import stat
import subprocess
import time
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT


def _start(work: Path, exit_code: int, tmpdir: Path, hold: Path | None = None):
    """tools/pytest.sh with a stub interpreter in `work`; it records the root it was handed in
    `work/argv`, and with `hold` waits (bounded) for that file before it exits."""
    bin_dir = work / "bin"
    bin_dir.mkdir(parents=True)
    argv = work / "argv"
    stub = bin_dir / "python3"
    wait = f'for _ in $(seq 300); do [ -e "{hold}" ] && break; sleep 0.1; done\n' if hold else ""
    stub.write_text(
        "#!/bin/bash\n"
        # -c: the xdist probe; `-m pytest --version`: the runner's pytest refusal (CI.PUBLIC2);
        # gates/ruff_agree.py: the one-ruff preflight (GOH.TOOLHYGIENE).
        # Only the suite's own run is recorded (a call after it, the timing ledger's row,
        # GOH.GATEBUDGET2, would overwrite it): `-c`, `--version` and every script exit 0.
        'if [ "$1 $2" != "-m pytest" ] || [ "$3" = --version ]; then\n'
        "  exit 0\nfi\n"
        f'printf "%s\\n" "$PYTEST_DEBUG_TEMPROOT" > "{argv}.part" && mv "{argv}.part" "{argv}"\n'
        f"{wait}exit {exit_code}\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    # The stub is the house interpreter through ITS seam (lib/house_python.sh, G2d): first on PATH
    # it is never asked, and the runner ran the REAL suite inside this test -- recursively, 33
    # nested suites deep, until the machine stalled (2026-10-25). GOH_PY_OK marks it resolved.
    env = {**os.environ, "GOH_PY": str(stub), "GOH_PY_OK": str(stub),
           "GATES_OF_HECK_SUITE_NESTED_OK": "1"}  # fmt: skip
    env["GOH_SUITE_CORES"] = "2"  # the count's seam: the stub answers no share (GOH.POOLSHARE)
    # A TMPDIR OF THE TEST'S OWN (GOH.BASETEMPSLOT, 2026-11-18): the runner's root is the lowest
    # free `goh-pytest.slotNN` under TMPDIR (GOH.FSCHURN). Under the suite's shared TMPDIR a
    # concurrent test's runner claimed the slot this run had just removed, and "the root is gone"
    # read a sibling's live root as this run's leftover (slot07, main 3ad21d54).
    tmpdir.mkdir(parents=True, exist_ok=True)
    env["TMPDIR"] = str(tmpdir)
    # the caller reaps it: communicate() in _run and in the concurrent test's finally
    return subprocess.Popen(
        ["bash", str(REPO_ROOT / "tools" / "pytest.sh")],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ), argv


def _run(tmp_path: Path, exit_code: int, tmpdir: Path | None = None):
    proc, argv = _start(tmp_path, exit_code, tmpdir or tmp_path / "tmp")
    out, _ = proc.communicate(timeout=timing_bounds.patience(120))
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, ""), Path(
        argv.read_text().strip()
    )


@pytest.mark.load_sensitive
def test_a_run_has_a_private_temp_root_and_a_passing_run_removes_it(tmp_path):
    done, base = _run(tmp_path, 0)
    assert done.returncode == 0, done.stdout + done.stderr
    # the runner's own slot, not pytest's shared `pytest-of-<user>` root
    assert base.parent == tmp_path / "tmp" / "goh.noindex", base
    assert base.name.startswith("goh-pytest."), base
    assert not base.exists(), f"{base} outlived a passing run"


@pytest.mark.load_sensitive
def test_a_failing_run_keeps_its_temp_root_and_its_exit(tmp_path):
    done, base = _run(tmp_path, 3)
    assert done.returncode == 3, done.stdout + done.stderr
    assert base.is_dir() and str(base) in done.stdout + done.stderr
    (base / ".goh-owner").unlink()  # the runner's pid: what keeps a live root from being cut
    base.rmdir()


@pytest.mark.load_sensitive
def test_concurrent_runs_take_distinct_slots_and_each_passing_run_removes_its_own(tmp_path):
    """The reuse contract (GOH.FSCHURN) under concurrent runs on ONE TMPDIR: each live run holds a
    slot of its own, each passing run removes its own, and a freed slot is the next run's."""
    shared, go = tmp_path / "tmp", tmp_path / "go"
    runs = [_start(tmp_path / name, 0, shared, hold=go) for name in ("a", "b")]
    try:
        give_up = timing_bounds.GiveUp(60)
        while not all(argv.exists() for _, argv in runs):
            assert not give_up.passed(), "the concurrent runs never reached pytest"
            assert all(p.poll() is None for p, _ in runs), [p.poll() for p, _ in runs]
            time.sleep(0.05)
        roots = [Path(argv.read_text().strip()) for _, argv in runs]
        assert sorted(r.name for r in roots) == ["goh-pytest.slot00", "goh-pytest.slot01"], roots
        assert all(r.is_dir() for r in roots), roots  # held while live
    finally:
        go.touch()
        outs = [p.communicate(timeout=timing_bounds.patience(120))[0] for p, _ in runs]
    assert [p.returncode for p, _ in runs] == [0, 0], outs
    left = sorted(p.name for p in (shared / "goh.noindex").iterdir() if p.name.startswith("goh-"))
    assert left == [], f"{left} outlived passing runs"
    # ...and a freed slot is the next run's, not a fresh name
    third, argv = _start(tmp_path / "c", 0, shared)
    out, _ = third.communicate(timeout=timing_bounds.patience(120))
    assert third.returncode == 0, out
    again = Path(argv.read_text().strip())
    assert again.name == "goh-pytest.slot00" and not again.exists(), again


PYTEST_RUN = re.compile(r"(^|[\s\"])(-m pytest|pytest -q|pytest tests)")


def test_every_script_that_starts_pytest_takes_a_private_root():
    """The class: any goh script that runs a suite, for this repo or a consumer's gate."""
    runners = []
    for path in sorted([*REPO_ROOT.glob("gates/*.sh"), *REPO_ROOT.glob("tools/*.sh")]):
        code = [l for l in path.read_text().splitlines() if not l.lstrip().startswith("#")]
        if any(PYTEST_RUN.search(l) for l in code):
            runners.append(path.relative_to(REPO_ROOT).as_posix())
            assert any("goh_pytest_root" in l for l in code), (
                f"{path.name} runs pytest on the shared root"
            )
    assert {"tools/pytest.sh", "gates/py_gate.sh", "gates/coverage_gate.sh"} <= set(runners), (
        runners
    )


def test_the_runner_refuses_to_start_inside_a_running_suite(tmp_path):
    """The recursion guard (G2d): with no explicit nested-ok, a runner inside a suite refuses, and
    never reaches pytest -- the stub records nothing."""
    stub = tmp_path / "python3"
    marker = tmp_path / "ran"
    stub.write_text(f'#!/bin/bash\nif [ "$1" = -c ]; then exit 0; fi\ntouch "{marker}"\n')
    stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "GATES_OF_HECK_SUITE_NESTED_OK"}
    env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_RUNNER="4242")
    done = subprocess.run(
        ["bash", str(REPO_ROOT / "tools" / "pytest.sh")],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert done.returncode == 2, done.stdout + done.stderr
    assert "inside a running goh suite" in done.stderr
    assert not marker.exists()
