"""A commit to a gates_of_heck checkout is judged by THAT checkout's gates (GOH.SELFGATE).

2026-11-11 (RUFF.RULES, 686548f): goh's own pre-commit reached GOH_DIR, the shared checkout, and
ran ITS HEAD's gates over a worktree commit that changed a gate's verdict over goh itself -- so a
gate change and the fix it requires could not land together without `GOH_DIR=<worktree>
GOH_LIVE=1`. gates/commit_gate.sh now hands a commit in a goh checkout to that checkout's own
commit gate, live (the tree being committed), with no override.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git

GATE = REPO_ROOT / "gates" / "commit_gate.sh"
STUB = '#!/usr/bin/env bash\necho "SELF-GATED dir=$GOH_DIR live=${GOH_LIVE:-}"\n'


def _goh_checkout(root: Path) -> Path:
    """A repository shaped like gates_of_heck, whose commit gate only says it ran."""
    root.mkdir()
    git(root, "init", "-q")
    (root / "gates").mkdir()
    (root / "gates" / "commit_gate.sh").write_text(STUB)
    (root / "gates" / "_from_head.sh").write_text("# a goh checkout\n")
    (root / "crates" / "goh").mkdir(parents=True)
    (root / "crates" / "goh" / "Cargo.toml").write_text('[package]\nname = "goh"\n')
    return root


def _commit_gate(cwd: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, **extra)
    env.pop("GOH_SELF_GATED", None)
    return subprocess.run(
        ["bash", str(GATE)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


def test_a_goh_worktree_commit_is_judged_by_its_own_gates(tmp_path: Path) -> None:
    other = _goh_checkout(tmp_path / "worktree")
    ran = _commit_gate(other, GOH_DIR=str(REPO_ROOT))
    real = os.path.realpath(other)
    assert f"SELF-GATED dir={real} live=1" in ran.stdout, (ran.returncode, ran.stdout, ran.stderr)
    assert ran.returncode == 0, ran.stderr


def test_a_consumer_commit_is_not_handed_anywhere(tmp_path: Path) -> None:
    repo = tmp_path / "consumer"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "gates").mkdir()
    (repo / "gates" / "commit_gate.sh").write_text(STUB)  # a gate of its own is not goh
    ran = _commit_gate(repo, GOH_DIR=str(REPO_ROOT))
    assert "SELF-GATED" not in ran.stdout, ran.stdout
