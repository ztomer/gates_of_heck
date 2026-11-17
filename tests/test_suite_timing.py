"""tools/pytest.sh writes the suite's cpu-s to the timing ledger (GOH.GATEBUDGET2).

Review 2026-11-17: "goh suite cpu-s a subject". The suite is most of a land's gate, and its wall
moves with the box's load (528 s at load 148, 1028 s at 24.7) while its CPU does not: a row per
WHOLE run (`goh suite`; `goh suite:carried` when the run reused another run's verdicts) carries the
cpu-s of the run's reaped tree, so `goh budget` can say the suite itself got dearer. A narrowed run
(`-k`, a file) measures a selection, never the suite, and writes nothing.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import timing_ledger

BURN = "import time\nt = time.process_time()\nwhile time.process_time() - t < 0.6: pass\n"


def _suite(tmp_path: Path, *args: str, **extra: str) -> list[dict]:
    """Run tools/pytest.sh with a stub interpreter whose `-m pytest` burns 0.6 s of CPU; the
    timing ledger's own call goes to the real interpreter. The rows the run wrote."""
    stub = tmp_path / "python3"
    burn = tmp_path / "burn.py"
    burn.write_text(BURN)
    stub.write_text(
        "#!/bin/bash\n"
        f'case "$*" in *timing_ledger.py*|*worker_share.py*) exec "{sys.executable}" "$@" ;; esac\n'
        'if [ "$1 $2" != "-m pytest" ] || [ "$3" = --version ]; then exit 0; fi\n'
        f'exec "{sys.executable}" "{burn}"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    ledger = tmp_path / "timing"
    drop = ("GOH_GATE_WORKERS", "GOH_GATE_SLOT", "GOH_LAND_VERDICTS_REUSE")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_NESTED_OK="1",
               GOH_SUITE_CORES="2", GOH_TIMING_DIR=str(ledger), **extra)  # fmt: skip
    done = subprocess.run(["bash", str(REPO_ROOT / "tools" / "pytest.sh"), *args], env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          timeout=patience(60))  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    return timing_ledger.read(ledger / "ledger.jsonl")


def test_a_whole_run_writes_one_suite_row_with_its_cpu_seconds(tmp_path: Path) -> None:
    rows = _suite(tmp_path)
    assert [(r["subject"], r["kind"], r["ok"]) for r in rows] == [("goh suite", "suite", True)]
    assert rows[0]["cpu_s"] >= 0.5, rows  # the stub's burn, read from the reaped tree


def test_a_run_reusing_verdicts_is_its_own_subject(tmp_path: Path) -> None:
    reuse = tmp_path / "verdicts"
    reuse.write_text("tests/test_a.py::test_x\n")
    rows = _suite(tmp_path, GOH_LAND_VERDICTS_REUSE=str(reuse))
    assert [r["subject"] for r in rows] == ["goh suite:carried"]


def test_a_narrowed_run_writes_no_row(tmp_path: Path) -> None:
    assert _suite(tmp_path, "-k", "plural_hedge") == []
