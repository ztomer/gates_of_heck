"""tools/pytest.sh hands its arguments to pytest (GOH.HOOKSEED, 2026-11-09).

It dropped them: `tools/pytest.sh -k plural` ran the whole suite, so a narrowed run cost what a full
one does and selected nothing. The stub interpreter below records the argv the runner hands pytest
(through the house-interpreter seam, never PATH: tests/test_pytest_runner_basetemp.py says why).
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from conftest import REPO_ROOT


def _argv(tmp_path: Path, *args: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "argv"
    stub = bin_dir / "python3"
    stub.write_text(
        f'#!/bin/bash\nif [ "$1" = -c ]; then exit 0; fi\nprintf "%s\\n" "$@" > "{record}"\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    env = {**os.environ, "GOH_PY": str(stub), "GOH_PY_OK": str(stub),
           "GATES_OF_HECK_SUITE_NESTED_OK": "1"}  # fmt: skip
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
