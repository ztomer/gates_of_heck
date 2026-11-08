"""A proven structural step still marks its tree for the push gate (GOH.SHX).

push_gate.sh runs the house structural layer itself when the repo's `tools/gate.sh --full` left no
GOH_STRUCTURAL_MARK naming the tree (BACKLOG 6.7). goh's own gate runs `./gates/structural.sh
--full` as a GOH_CI_STEPS step, but land.sh proves the tree first, so at the push that step is a
proven-cache hit: structural.sh never ran, nothing marked, and every push warned "passed without
running the house structural layer" and ran it a second time. A hit stands for the run it skipped,
mark included -- and only for a step that IS a structural run of this tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_proven import _git, env, goh, repo  # noqa: F401  (fixtures)


def _ci(gates: Path, proj: Path, environ: dict, mark: Path) -> str:
    r = subprocess.run(
        ["bash", str(gates / "gates" / "local_ci.sh"), "."],
        cwd=proj,
        env={**environ, "GOH_STRUCTURAL_MARK": str(mark)},
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout + r.stderr


def _declare(proj: Path, steps: str) -> None:
    (proj / ".gatesrc").write_text(f"GOH_CI_STEPS='{steps}'\n")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "steps")


def test_a_proven_structural_step_marks_the_tree_it_stands_for(goh, repo, env, tmp_path):  # noqa: F811
    _declare(repo, 'bash "$GOH_DIR/gates/structural.sh" --full')
    first = tmp_path / "first"
    _ci(goh, repo, env, first)
    top = _git(repo, "rev-parse", "--show-toplevel")
    assert first.read_text() == f"--full\t{top}\n"
    second = tmp_path / "second"
    out = _ci(goh, repo, env, second)
    assert "proven on this tree" in out, out
    assert second.exists() and second.read_text() == f"--full\t{top}\n", out


def test_a_proven_step_that_is_not_structural_marks_nothing(goh, repo, env, tmp_path):  # noqa: F811
    _declare(repo, 'echo ran >> "$COUNT":echo structural.sh --full')
    _ci(goh, repo, env, tmp_path / "first")
    second = tmp_path / "second"
    out = _ci(goh, repo, env, second)
    assert "proven on this tree" in out, out
    assert not second.exists() or second.read_text() == "", second.read_text()
