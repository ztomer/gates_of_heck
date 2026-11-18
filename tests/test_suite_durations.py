"""tools/pytest.sh names the duration table for a top-level run only (GOH.DURATIONSSTALE).

The table (lib/fast_tests.py `cache()`, goh's state) orders the suite longest first and chooses the
land pre-step's fast judges. A run nested on purpose (GATES_OF_HECK_SUITE_NESTED_OK) is inside the
holder's run, so it is named none, even when the variable reaches it. The stub interpreter runs the
runner's helpers for real and records what pytest's run was handed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from timing_bounds import patience

from lib import fast_tests

RUNNER = REPO_ROOT / "tools" / "pytest.sh"


def _handed(tmp_path: Path, **extra: str) -> str:
    """What the runner's pytest run saw in GOH_TEST_DURATIONS (`unset` when it saw none)."""
    tmp_path.mkdir()
    seen = tmp_path / "seen"
    stub = tmp_path / "python3"
    stub.write_text(
        "#!/bin/bash\n"
        f'case "$1" in -S | -c) exec "{sys.executable}" "$@" ;; esac\n'
        'if [ "$1 $2" != "-m pytest" ] || [ "$3" = --version ]; then exit 0; fi\n'
        f'printf %s "${{GOH_TEST_DURATIONS-unset}}" > "{seen}"\n'
    )
    stub.chmod(0o755)
    drop = ("GATES_OF_HECK_SUITE_RUNNER", "GATES_OF_HECK_SUITE_NESTED_OK", "GOH_GATE_SLOT")
    env = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GOH_HELD")}
    env.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GOH_SUITE_CORES="2", GOH_GATE_SLOTS="1",
               GATES_OF_HECK_SUITE_LOCK=str(tmp_path / "goh-suite.lock"),
               GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), GOH_SUITE_WAIT_MAX="60",
               **extra)  # fmt: skip
    done = subprocess.run(["bash", str(RUNNER), "tests/test_suite_lock.py"], env=env,
                          cwd=REPO_ROOT, capture_output=True, text=True, check=False,
                          stdin=subprocess.DEVNULL, timeout=patience(120))  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
    return seen.read_text()


def test_a_top_level_run_is_named_the_table_and_a_nested_run_none(tmp_path: Path) -> None:
    assert os.environ.get("GOH_TEST_DURATIONS") is None  # popped by this suite's own top-level run
    assert _handed(tmp_path / "top") == str(fast_tests.cache())
    nested = _handed(tmp_path / "nested", GATES_OF_HECK_SUITE_RUNNER=str(os.getpid()),
                     GATES_OF_HECK_SUITE_NESTED_OK="1",
                     GOH_TEST_DURATIONS="/inherited.json")  # fmt: skip
    assert nested == "unset"
