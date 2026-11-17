"""tools/pytest.sh hands its arguments to pytest (GOH.HOOKSEED, 2026-11-09).

It dropped them: `tools/pytest.sh -k plural` ran the whole suite, so a narrowed run cost what a full
one does and selected nothing. The stub interpreter below records the argv the runner hands pytest
(through the house-interpreter seam, never PATH: tests/test_pytest_runner_basetemp.py says why).
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT


def _argv(tmp_path: Path, *args: str, cores: str | None = None, **extra: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv"
    stub = bin_dir / "python3"
    # The share (lib/worker_share.py) is asked of the REAL interpreter: it is what is under test.
    stub.write_text(
        f'#!/bin/bash\ncase "$*" in *worker_share.py*) exec "{sys.executable}" "$@" ;; esac\n'
        f'if [ "$1" = -c ]; then exit 0; fi\nprintf "%s\\n" "$@" > "{record}"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "slots").mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("GOH_GATE_WORKERS", "GOH_GATE_SLOT")}
    env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_NESTED_OK="1")
    env.update(GOH_GATE_CORES="16", GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), **extra)
    if cores is not None:
        env["GOH_SUITE_CORES"] = cores
    done = subprocess.run(["bash", str(REPO_ROOT / "tools" / "pytest.sh"), *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          timeout=60)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    return record.read_text().splitlines()


def test_a_k_filter_reaches_pytest_and_the_suite_stays_the_target(tmp_path):
    argv = _argv(tmp_path, "-k", "plural_hedge")
    assert argv[:2] == ["-m", "pytest"], argv
    assert "-k" in argv and argv[argv.index("-k") + 1] == "plural_hedge", argv
    assert "tests/" in argv, argv


def test_a_named_test_file_is_the_target_in_place_of_the_whole_suite(tmp_path):
    argv = _argv(tmp_path, "tests/test_ship.py::test_a_dirty_tree_is_refused_before_it_starts")
    assert "tests/test_ship.py::test_a_dirty_tree_is_refused_before_it_starts" in argv, argv
    assert "tests/" not in argv, argv


def test_no_argument_runs_the_whole_suite(tmp_path):
    argv = _argv(tmp_path)
    assert "tests/" in argv and "-k" not in argv, argv


def test_the_worker_count_follows_the_machines_cores(tmp_path):
    """Run 42950563400: `-n 12` on a 3-core hosted runner starved a cold `goh build` and a
    structural --full past their 600 s timeouts. The workers are the machine's cores, capped at the
    12 measured on the owner's box (GOH_SUITE_CORES is the seam the count is read through)."""
    (tmp_path / "few").mkdir()
    (tmp_path / "many").mkdir()
    few = _argv(tmp_path / "few", cores="3")
    many = _argv(tmp_path / "many", cores="32")
    assert few[few.index("-n") + 1] == "3", few
    assert many[many.index("-n") + 1] == "12", many


def test_inside_a_gate_the_workers_are_its_admissions_share(tmp_path):
    """GOH.POOLSHARE: a suite sized to all 16 cores beside four other gates sized the same put ~220
    threads on 16 cores (2026-11-17). Inside a gate the count is the admission's share."""
    argv = _argv(tmp_path, GOH_GATE_WORKERS="5")
    assert argv[argv.index("-n") + 1] == "5", argv


def test_outside_any_gate_the_workers_are_a_share_beside_the_admitted_gates(tmp_path):
    """A land's pre-step runs this under no slot: with one gate admitted it is the second of two,
    so 16 cores give it 8 workers, not the cap of 12."""
    (tmp_path / "slots").mkdir()
    other = open(tmp_path / "slots" / "slot-0", "w")  # noqa: SIM115  -- another gate's slot
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        argv = _argv(tmp_path)
    finally:
        other.close()
    assert argv[argv.index("-n") + 1] == "8", argv
