"""A pre-step that reaches its deadline is no verdict; the gate decides (GOH.PRESTEPDEADLINE).

GOH.REUSEAUDIT's land (2026-11-17) was killed by the pre-step's 10 min deadline at 82% of a
green-so-far run and printed 'the pre-land tests failed ... nothing landed'. The pre-step exists to
fail FAST; one that ran out of time found nothing, and the gate runs the whole suite anyway.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import git, hermetic_env
from test_affected_tests import _declare
from test_land import LAND, _main, _worktree
from timing_bounds import patience


def _land(repo: Path, tmp_path: Path, pre: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='{pre}'\n")
    env = hermetic_env(drop_git=True, GOH_LAND_PRE_MAX="2s", GOH_LAND_PRE_CEIL="2s")
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    r = subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=patience(120), check=False)  # fmt: skip
    return r, wt


def test_a_pre_step_past_its_deadline_lets_the_gate_decide(repo: Path, tmp_path: Path) -> None:
    r, wt = _land(repo, tmp_path, "sleep 30")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "pre-land: no verdict after " in out and "the gate decides" in out, out
    assert "failed" not in out, out
    assert (tmp_path / "seen").read_text() == "gate\n"
    assert _main(repo) == git(wt, "rev-parse", "HEAD").strip()


def test_a_pre_step_that_fails_still_lands_nothing(repo: Path, tmp_path: Path) -> None:
    r, wt = _land(repo, tmp_path, "exit 1")
    assert r.returncode == 1 and "the pre-land tests failed" in r.stdout + r.stderr
    assert not (tmp_path / "seen").exists() and _main(repo) != git(wt, "rev-parse", "HEAD").strip()
