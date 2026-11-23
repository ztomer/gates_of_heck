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


def _verdicts(tmp_path: Path, goh: Path, body: str) -> tuple[subprocess.CompletedProcess[str], ...]:
    repo = _repo(tmp_path)
    (repo / "tool.py").write_text(body)
    lint = _run(repo, str(goh), "lint", "tool.py")
    assert _git(repo, "diff", "--cached", "--name-only") == "", "a lint stages nothing"
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "add", "tool.py")
    commit = _run(repo, "git", "commit", "-q", "-m", "plant")
    return lint, commit, head == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(("body", "code"), [(E501, "E501"), (SIM105, "SIM105")])
def test_lint_and_commit_refuse_the_same_planted_finding(tmp_path, goh, body, code):
    lint, commit, unmoved = _verdicts(tmp_path, goh, body)
    lint_out, commit_out = lint.stdout + lint.stderr, commit.stdout + commit.stderr
    assert commit.returncode != 0 and unmoved, commit_out
    assert lint.returncode == commit.returncode, (lint_out, commit_out)
    assert code in lint_out and code in commit_out, (lint_out, commit_out)


def test_lint_and_commit_pass_the_same_clean_file(tmp_path, goh):
    lint, commit, unmoved = _verdicts(tmp_path, goh, CLEAN)
    assert lint.returncode == 0 == commit.returncode, lint.stdout + lint.stderr
    assert "nothing committed" in lint.stdout + lint.stderr
    assert not unmoved, "the commit landed"


def test_plain_ruff_passes_what_the_commit_refuses(tmp_path, goh):
    """The instrument this replaces, shown blind: plain `ruff check` is green on the SIM105 plant
    that the commit gate (and so `goh lint`) refuses."""
    repo = _repo(tmp_path)
    (repo / "tool.py").write_text(SIM105)
    assert _run(repo, "ruff", "check", "--isolated", "tool.py").returncode == 0
    assert _run(repo, str(goh), "lint", "tool.py").returncode != 0


def test_no_path_lints_every_file_changed_against_head(tmp_path, goh):
    """GOH.LINTASGATE: `goh lint` with no path is the commit of everything changed -- the edited
    tracked files and the new untracked ones -- so the one command needs no list typed by hand."""
    repo = _repo(tmp_path)
    (repo / "tool.py").write_text(SIM105)
    lint = _run(repo, str(goh), "lint")
    assert lint.returncode != 0 and "SIM105" in lint.stdout + lint.stderr, lint.stdout + lint.stderr
    (repo / "tool.py").write_text(CLEAN)
    lint = _run(repo, str(goh), "lint")
    assert lint.returncode == 0, lint.stdout + lint.stderr
    _git(repo, "add", "tool.py")
    _git(repo, "commit", "-q", "--no-verify", "-m", "clean")
    lint = _run(repo, str(goh), "lint")
    assert lint.returncode == 0 and "nothing changed against HEAD" in lint.stdout + lint.stderr
