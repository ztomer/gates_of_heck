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
        # Only the suite's own run is recorded: a call after it (the timing ledger's row,
        # GOH.GATEBUDGET2) would overwrite the argv under test.
        f'if [ "$1 $2" != "-m pytest" ] || [ "$3" = --version ]; then exit 0; fi\n'
        f'printf "%s\\n" "$@" > "{record}"\n'
        f'env | grep "^GOH_SHARE_" > "{record}.env" || true\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "slots").mkdir(exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GOH_GATE_WORKERS", "GOH_GATE_SLOT") and not k.startswith("GOH_SHARE_")
    }
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


def _share_env(tmp_path: Path) -> dict[str, str]:
    lines = (tmp_path / "argv.env").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_inside_a_gate_the_run_starts_at_its_ceiling_on_a_live_token_pool(tmp_path):
    """GOH.SHAREGROW: the admission's fixed share (5 here) no longer sizes xdist; the run starts at
    the ceiling and its workers take tokens from the share as it is NOW (test_share_grow.py)."""
    argv = _argv(tmp_path, GOH_GATE_WORKERS="5", GOH_SHARE_OWN="0")
    assert argv[argv.index("-n") + 1] == "12", argv
    env = _share_env(tmp_path)
    assert env["GOH_SHARE_CEIL"] == "12" and env["GOH_SHARE_OWN"] == "0", env
    assert Path(env["GOH_SHARE_POOL"]).name == "share-pool", env


def test_outside_any_gate_the_pool_counts_the_run_as_one_more_beside_the_admitted(tmp_path):
    """A land's pre-step runs this under no slot: with one gate admitted it is the second of two,
    so its tokens are 8 of 16 cores (GOH_SHARE_OWN=1), not the ceiling of 12."""
    (tmp_path / "slots").mkdir()
    other = open(tmp_path / "slots" / "slot-0", "w")  # noqa: SIM115  -- another gate's slot
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        argv = _argv(tmp_path)
    finally:
        other.close()
    assert argv[argv.index("-n") + 1] == "12", argv
    assert _share_env(tmp_path)["GOH_SHARE_OWN"] == "1"
