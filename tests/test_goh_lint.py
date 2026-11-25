"""`goh lint <path>...` gives the commit gate's verdict before the commit (GOH.CLASS.PARITY).

An agent ran plain `ruff check`, saw green, and the commit was refused: the hook judges with the
house rule set (gates/ruff_house.py) under per-file ceilings and the pinned ruff. `goh lint` is no
second implementation of the hook -- it runs gates/commit_gate.sh itself over the index a
`git commit -- <path>...` would commit (tools/lint.py). These tests plant the two findings the owner
named, E501 and SIM105, in a consumer whose own gate runs only structural, and hold `goh lint` and
the real commit to the SAME verdict on each, and on a clean file; then show a lint commits nothing
and leaves the repository's index as it was.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_commit_gate_structural import _consumer, _git

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")

GATE_STRUCTURAL_ONLY = (
    '#!/usr/bin/env bash\nset -euo pipefail\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'
)
E501 = "X = " + '"' + "x" * 120 + '"\n'
SIM105 = "import os\n\ntry:\n    os.remove('a')\nexcept OSError:\n    pass\n"
CLEAN = "X = 1\n"


def _repo(tmp_path: Path) -> Path:
    repo = _consumer(tmp_path, GATE_STRUCTURAL_ONLY)
    # The house rule set applies under ceilings (gates/py_staged.sh); an empty table holds no debt.
    (repo / "ceilings.json").write_text("{}\n")
    (repo / ".gatesrc").write_text("GOH_PY_LINT_CEILINGS=ceilings.json\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "ceilings")
    return repo


def _run(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=repo, env=hermetic_env(GOH_DIR=str(REPO_ROOT)), capture_output=True,
        text=True, timeout=300, stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_lint_and_commit_refuse_the_same_planted_findings(tmp_path, goh):
    """One dirty tree for both findings and for the no-path form (GOH.LINTASGATE: `goh lint` is the
    commit of everything changed, so the E501 sits in an edited tracked file and the SIM105 in a
    new untracked one). Plain `ruff check`, the instrument this replaces, is green on the SIM105."""
    repo = _repo(tmp_path)
    (repo / "old.py").write_text(CLEAN)
    _git(repo, "add", "old.py")
    _git(repo, "commit", "-q", "--no-verify", "-m", "old")
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "old.py").write_text(E501)
    (repo / "tool.py").write_text(SIM105)
    assert _run(repo, "ruff", "check", "--isolated", "tool.py").returncode == 0
    lint = _run(repo, str(goh), "lint")
    assert _git(repo, "diff", "--cached", "--name-only") == "", "a lint stages nothing"
    _git(repo, "add", "old.py", "tool.py")
    commit = _run(repo, "git", "commit", "-q", "-m", "plant")
    lint_out, commit_out = lint.stdout + lint.stderr, commit.stdout + commit.stderr
    assert commit.returncode != 0 and head == _git(repo, "rev-parse", "HEAD"), commit_out
    assert lint.returncode == commit.returncode, (lint_out, commit_out)
    for code in ("E501", "SIM105"):
        assert code in lint_out and code in commit_out, (lint_out, commit_out)


def test_lint_and_commit_pass_the_same_clean_file(tmp_path, goh):
    repo = _repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    (repo / "tool.py").write_text(CLEAN)
    (repo / "other.py").write_text(SIM105)  # changed, and not among the paths named
    lint = _run(repo, str(goh), "lint", "tool.py")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    assert "nothing committed" in lint.stdout + lint.stderr
    assert _git(repo, "diff", "--cached", "--name-only") == "", "a lint stages nothing"
    (repo / "other.py").unlink()
    _git(repo, "add", "tool.py")
    commit = _run(repo, "git", "commit", "-q", "-m", "plant")
    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert head != _git(repo, "rev-parse", "HEAD"), "the commit landed"
    lint = _run(repo, str(goh), "lint")
    assert lint.returncode == 0 and "nothing changed against HEAD" in lint.stdout + lint.stderr
