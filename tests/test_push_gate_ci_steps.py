"""The steps a repo DECLARES in GOH_CI_STEPS run at push, whatever its tools/gate.sh calls.

homebrew-tap's `tools/gate.sh --full` ran the structural layer alone, so the tap's consistency
and `brew style` checks sat in `.gatesrc` as GOH_CI_STEPS and were executed by nothing from the
day they were declared (found 2026-10-18, CadGoose G0.18); divoom-control had the same hole
until its R71 P0.1. Same shape as the structural mark (6.7): local_ci.sh marks the tree whose
declared steps it ran, and a gate that passed without that mark gets them run after it, with
the gap named. A local_ci run in some OTHER tree does not count for the repo's own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _fast_git import fast_init
from test_push_gate_race import _push

GAP = "without running the steps .gatesrc declares"
STRUCTURAL = 'bash "$GOH_DIR/gates/structural.sh" --full\n'
SKIPS = "#!/usr/bin/env bash\nset -euo pipefail\n" + STRUCTURAL
CALLS = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'exec bash "$GOH_DIR/gates/local_ci.sh" "$(cd "$(dirname "$0")/.." && pwd)"\n'
)
ELSEWHERE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    + STRUCTURAL
    + 'bash "$GOH_DIR/gates/local_ci.sh" --step true "$SCRATCH_FIXTURE"\n'
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _consumer(tmp_path: Path, gate: str, steps: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    fast_init(fixture)
    (repo / "tools" / "gate.sh").write_text(gate.replace("$SCRATCH_FIXTURE", str(fixture)))
    (repo / "README.md").write_text("consumer\n")
    if steps is not None:
        (repo / ".gatesrc").write_text(f"GOH_CI_STEPS='{steps}'\n")
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "one")
    return repo


def _run(tmp_path: Path, gate: str, steps: str | None) -> tuple[int, str]:
    got = _push(_consumer(tmp_path, gate, steps), tmp_path, "refs/heads/main")
    return got.returncode, got.stdout + got.stderr


def test_a_declared_step_the_gate_never_runs_still_fails_the_push(tmp_path):
    code, text = _run(tmp_path, SKIPS, "false")
    assert code != 0, text
    assert GAP in text, text


def test_a_gate_that_skips_its_declared_steps_names_the_gap_when_they_pass(tmp_path):
    code, text = _run(tmp_path, SKIPS, "true")
    assert code == 0, text
    assert GAP in text, text


def test_a_gate_that_runs_its_declared_steps_runs_them_once_and_says_nothing(tmp_path):
    code, text = _run(tmp_path, CALLS, '"$GOH_DIR/gates/structural.sh" --full:true')
    assert code == 0 and GAP not in text, text


def test_a_repo_that_declares_no_steps_is_not_asked_for_them(tmp_path):
    code, text = _run(tmp_path, SKIPS, None)
    assert code == 0 and GAP not in text, text


def test_local_ci_in_another_tree_does_not_stand_in_for_the_repos(tmp_path):
    code, text = _run(tmp_path, ELSEWHERE, "false")
    assert code != 0 and GAP in text, text
