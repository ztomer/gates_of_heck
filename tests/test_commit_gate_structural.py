"""The house structural layer runs on every commit and push, whatever the repo's gate calls (6.7).

old_phones' `tools/gate.sh` never called structural.sh, so emoji and the line cap were enforced
nowhere there. The stock pre-commit now delegates to gates/commit_gate.sh, and push_gate.sh does
the same for a push: structural.sh marks what it ran (scope and tree), and a gate that passed
without the mark gets structural run after it, with the gap named. A structural run inside some
OTHER tree -- a consumer's fixture -- does not count for the repo's own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import EMOJI_SMILE, REPO_ROOT, hermetic_env
from test_push_gate_race import _push

SKIPS = "#!/usr/bin/env bash\nexit 0\n"
CALLS = (
    '#!/usr/bin/env bash\nset -euo pipefail\nexec bash "$GOH_DIR/gates/structural.sh" --staged\n'
)
# Runs structural in a scratch repo of its own, as a consumer's test suite might, and nothing here.
ELSEWHERE = """#!/usr/bin/env bash
set -euo pipefail
(cd "$SCRATCH/fixture" && bash "$GOH_DIR/gates/structural.sh" --staged)
"""
GAP = "without running the house structural layer"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _consumer(tmp_path: Path, gate: str) -> Path:
    repo = tmp_path / "repo"
    (repo / ".githooks").mkdir(parents=True)
    hook = repo / ".githooks" / "pre-commit"
    hook.write_bytes((REPO_ROOT / "hooks" / "pre-commit").read_bytes())
    hook.chmod(0o755)
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(gate)
    (repo / "README.md").write_text("consumer\n")
    fast_init(repo, "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "one")
    _git(repo, "config", "core.hooksPath", ".githooks")
    return repo


def _commit(repo: Path, tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    (repo / "notes.md").write_text(body)
    _git(repo, "add", "notes.md")
    if not (tmp_path / "scratch" / "fixture").exists():
        (tmp_path / "scratch" / "fixture").mkdir(parents=True)
        fast_init(tmp_path / "scratch" / "fixture")
    env = hermetic_env(GOH_DIR=str(REPO_ROOT), SCRATCH=str(tmp_path / "scratch"))
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "ours"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_gate_that_skips_structural_still_meets_it_at_commit(tmp_path):
    got = _commit(_consumer(tmp_path, SKIPS), tmp_path, f"party {EMOJI_SMILE}\n")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert GAP in text and "notes.md" in text, text


def test_a_gate_that_skips_structural_names_the_gap_on_a_clean_commit(tmp_path):
    got = _commit(_consumer(tmp_path, SKIPS), tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert GAP in text, text


def test_a_gate_that_calls_structural_runs_it_once_and_says_nothing(tmp_path):
    got = _commit(_consumer(tmp_path, CALLS), tmp_path, "plain\n")
    text = got.stdout + got.stderr
    assert got.returncode == 0 and GAP not in text, text


def test_structural_in_another_tree_does_not_stand_in_for_the_repos(tmp_path):
    got = _commit(_consumer(tmp_path, ELSEWHERE), tmp_path, f"party {EMOJI_SMILE}\n")
    text = got.stdout + got.stderr
    assert got.returncode != 0 and GAP in text, text


def test_a_gate_that_skips_structural_still_meets_it_at_push(tmp_path):
    repo = _consumer(tmp_path, SKIPS)
    (repo / "notes.md").write_text(f"party {EMOJI_SMILE}\n")
    _git(repo, "add", "notes.md")
    _git(repo, "commit", "-q", "--no-verify", "-m", "emoji past the commit hook")
    got = _push(repo, tmp_path, "refs/heads/main")
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert GAP in text and "notes.md" in text, text
