"""Every `subprocess.run` here says what a failing child does (G16, ruff PLW1510).

`subprocess.run` without `check=` is ambiguous: a reader cannot tell "the exit code is judged below"
from "a failure is silently ignored". 325 calls here left it unsaid, and the repo's own gate never
ran `ruff check`, so nothing said so; the rule was only red under ruff's defaults, which no step used.
pyproject.toml now selects PLW1510 for THIS repo and GOH_CI_STEPS runs `ruff check .`. Consumers keep
their own rule sets (the shared py_gate.sh runs the project's configuration), so this does not turn
any of them red.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tomllib

import pytest

from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")

BAD = "import subprocess\nsubprocess.run(['true'], stdin=subprocess.DEVNULL)\n"
GOOD = "import subprocess\nsubprocess.run(['true'], stdin=subprocess.DEVNULL, check=False)\n"


def _ruff(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ruff", "check", "--no-cache", "--output-format", "concise", *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_repo_config_selects_the_rule() -> None:
    cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "PLW1510" in cfg["tool"]["ruff"]["lint"]["select"]


def test_the_configured_rule_bites_a_call_without_check(tmp_path) -> None:
    f = tmp_path / "bad.py"
    f.write_text(BAD, encoding="utf-8")
    r = _ruff("--config", str(REPO_ROOT / "pyproject.toml"), str(f))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "PLW1510" in r.stdout


def test_an_explicit_check_passes(tmp_path) -> None:
    f = tmp_path / "good.py"
    f.write_text(GOOD, encoding="utf-8")
    r = _ruff("--config", str(REPO_ROOT / "pyproject.toml"), str(f))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_push_gate_runs_ruff_check() -> None:
    gatesrc = (REPO_ROOT / ".gatesrc").read_text(encoding="utf-8")
    steps = re.search(r"^GOH_CI_STEPS='(.+)'$", gatesrc, re.M)
    assert steps, "GOH_CI_STEPS is not set"
    assert "ruff check ." in [s.strip() for s in steps.group(1).split(":")]


def test_fixtures_are_data_and_stay_as_written() -> None:
    """tests/fixtures hold sources that checkers are run AGAINST; the rule must not rewrite them
    (an edit there changed `test_py_marker_exempts`'s subject, red on the first push of G16)."""
    r = _ruff("--config", str(REPO_ROOT / "pyproject.toml"), str(REPO_ROOT / "tests" / "fixtures"))
    assert r.returncode == 0, r.stdout + r.stderr
