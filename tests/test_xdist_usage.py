"""An xdist worker's collection error reaches the run (tests/_xdist_usage.py, GOH.REMOTEREDSILENT).

THE INCIDENT (2026-11-23). Two lanes' land pre-steps went red four times reading only `bringing up
nodes...`: main's idle-suite red named `...is_never_taken[some` (its param id `[some other source]`
cut at the space), the node joined the pre-step's set, and under xdist a node id that is not found
ends every worker's collection with pytest's usage error, which xdist drops: `0 items`, exit 5,
and at `-qq` not one word. Run here at -qq, as tools/pytest.sh runs it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

XDIST = (
    ("-n", "2") if "xdist" in os.environ.get("PYTEST_PLUGINS", "") else ("-p", "xdist", "-n", "2")
)


def _run(tmp_path: Path, *nodes: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_planted.py").write_text(
        "import pytest\n\n\n@pytest.mark.parametrize('src', ['some other source'])\n"
        "def test_taken(src):\n    pass\n\n\ndef test_plain():\n    pass\n"
    )
    env = hermetic_env(PYTHONPATH=str(REPO_ROOT / "tests"))
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-q", "-p", "no:cacheprovider", *XDIST,
         "-p", "_xdist_usage", *nodes],
        cwd=tmp_path, capture_output=True, text=True, timeout=patience(120), check=False,
        env=env, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_a_node_id_no_worker_finds_fails_the_run_naming_it(tmp_path: Path) -> None:
    r = _run(tmp_path, "test_planted.py::test_plain", "test_planted.py::test_taken[some")
    said = r.stdout + r.stderr
    assert r.returncode == 4, said[-1500:]
    assert "not found: " in said and "test_planted.py::test_taken" in said, said[-1500:]


def test_found_node_ids_run_as_before(tmp_path: Path) -> None:
    r = _run(
        tmp_path, "test_planted.py::test_plain", "test_planted.py::test_taken[some other source]"
    )
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert "not found" not in r.stdout + r.stderr
