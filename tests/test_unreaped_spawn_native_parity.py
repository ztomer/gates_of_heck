"""`goh unreaped-spawn` is a port, so the measured table must read the same through both (Phase N1).

The table in `checks/_unreaped_spawn_table*.py` IS the specification (R2), and `--probe` runs it
through the Python checker only. Here every row goes through both, and the WHOLE verdict list is
compared -- line, tag and reason -- not just finding-or-clean, so a port that reaches the right
answer for the wrong reason shows. The whole-tree report on this repository, the largest real
corpus the suite owns, is compared byte for byte at both scopes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO_ROOT

from reference_kit import load_reference, reference_path  # noqa: E402

from unreaped_spawn_table import PYTHON, PYTHON_EXTRA, RUST, SHELL  # noqa: E402
from unreaped_spawn_table_regressions import (  # noqa: E402
    PYTHON_RAW_QUOTE_CALIBRATION,
    PYTHON_REGRESSIONS,
    RUST_REGRESSIONS,
)

verdicts = load_reference("check_no_unreaped_spawn").verdicts

ROWS = (
    [(".rs", label, src) for label, src, _ in RUST + RUST_REGRESSIONS]
    + [
        (".py", label, src)
        for label, src, _ in PYTHON
        + PYTHON_EXTRA
        + PYTHON_REGRESSIONS
        + [PYTHON_RAW_QUOTE_CALIBRATION]
    ]
    + [(".sh", label, src) for label, src, _ in SHELL]
)


def native(goh: Path, source: str, ext: str) -> list[list]:
    r = subprocess.run(
        [str(goh), "unreaped-spawn", "--verdicts", ext],
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize(
    ("ext", "label", "source"), ROWS, ids=[f"{e}:{l[:60]}" for e, l, _ in ROWS]
)
def test_every_table_row_reads_the_same_through_the_port(
    goh: Path, ext: str, label: str, source: str
) -> None:
    want = [list(v) for v in verdicts(source, ext)]
    assert native(goh, source, ext) == want, label


def test_the_table_is_not_empty() -> None:
    """A parity table over zero rows agrees with everything."""
    assert len(ROWS) >= 55, len(ROWS)


def _report(cmd: list[str]) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "FORCE_COLOR")}
    env["NO_COLOR"] = "1"
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize("scope", [[], ["--staged"]], ids=["full", "staged"])
def test_this_repositorys_report_is_byte_identical(goh: Path, scope: list[str]) -> None:
    py = _report([sys.executable, str(reference_path("checks/check_no_unreaped_spawn.py")), *scope])
    rs = _report([str(goh), "unreaped-spawn", *scope])
    assert rs == py
