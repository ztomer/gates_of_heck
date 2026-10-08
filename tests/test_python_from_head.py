"""A Python checker called DIRECTLY from the shared checkout runs HEAD's copy (C4 residual).

The bash entry points re-run themselves from an export of HEAD (`gates/_from_head.sh`); a Python
checker called by path -- `python3 "$GOH_DIR/checks/check_no_emoji.py"`, how CadGoose2's step list
and koffee_big's gates_lint.sh call them -- went around it and judged with the shared working tree.
Planted here: an uncommitted edit to a checker, on a scratch clone of the gates.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from test_gate_environment import _clean_checkout_of_todays_gates

PLANT = "LIVE-PYTHON-CHECKER-EDIT-7714"
NOTICE = "running HEAD's copy"
STANZA = re.compile(
    r'^if __name__ == "__main__":.*\n(?:    .*\n|\n)*?\s+__import__\("_from_head"\)\.reexec\(__file__\)',
    re.M,
)


def test_every_python_entry_point_runs_from_head() -> None:
    entries = [
        p
        for base in ("checks", "gates", "lib")
        for p in sorted((REPO_ROOT / base).glob("*.py"))
        if '\nif __name__ == "__main__":' in p.read_text(encoding="utf-8")
    ]
    assert len(entries) >= 20, entries  # 47 until Phase N3 retired the ported checkers
    missing = [p.name for p in entries if not STANZA.search(p.read_text(encoding="utf-8"))]
    assert not missing, f"a script entry point that judges with the live tree: {missing}"


@pytest.fixture(scope="module")
def gates(tmp_path_factory) -> Path:
    return _clean_checkout_of_todays_gates(tmp_path_factory.mktemp("g"))


@pytest.fixture
def dirty(gates: Path):
    checker = gates / "checks" / "loc_of_baseline_files.py"
    before = checker.read_text()
    checker.write_text(before.replace("def main", f'print("{PLANT}")\n\n\ndef main', 1))
    yield gates
    checker.write_text(before)


def _run(gates: Path, repo: Path, tmp_path: Path, **extra: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_", "PYTEST_"))}
    env.update(GOH_HEAD_CACHE=str(tmp_path / "head-cache"), **extra)
    return subprocess.run(
        ["python3", str(gates / "checks" / "loc_of_baseline_files.py"), "b.txt"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_a_direct_call_judges_with_head_and_goh_live_with_the_tree(
    dirty: Path, repo: Path, tmp_path
) -> None:
    (repo / "a.txt").write_text("fine\n")
    (repo / "b.txt").write_text("1\ta.txt\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    head = _run(dirty, repo, tmp_path)
    assert head.returncode == 0, head.stdout + head.stderr
    assert PLANT not in head.stdout + head.stderr, "an uncommitted checker edit judged a consumer"
    assert NOTICE not in head.stderr, "the shared checkout is what consumers call: no notice there"
    live = _run(dirty, repo, tmp_path, GOH_LIVE="1")
    assert PLANT in live.stdout, "GOH_LIVE=1 must run the working tree"


def test_a_linked_worktree_says_its_edit_is_not_what_ran(gates: Path, repo: Path, tmp_path) -> None:
    """A worktree is where the gates are developed, and no consumer's GOH_DIR points into one.

    There, HEAD's copy silently judging in place of the edit read as the edit passing: a checker
    fix was run against CadGoose from its worktree, reported the old verdict, and was debugged as
    a wrong fix (2026-10-08). The shared checkout stays quiet (the test above): a peer's half-done
    edit there is exactly the noise C4 removed from consumers' gates.
    """
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(gates), "worktree", "add", "-q", str(wt)], check=True)
    (repo / "a.txt").write_text("fine\n")
    (repo / "b.txt").write_text("1\ta.txt\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    clean = _run(wt, repo, tmp_path)
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert NOTICE not in clean.stderr, "an unedited file ran as committed: nothing to say"
    checker = wt / "checks" / "loc_of_baseline_files.py"
    checker.write_text(
        checker.read_text().replace("def main", f'print("{PLANT}")\n\n\ndef main', 1)
    )
    head = _run(wt, repo, tmp_path)
    assert head.returncode == 0, head.stdout + head.stderr
    assert PLANT not in head.stdout, "HEAD's copy still judges"
    assert NOTICE in head.stderr and "checks/loc_of_baseline_files.py" in head.stderr, head.stderr
    assert "GOH_LIVE=1" in head.stderr, head.stderr
