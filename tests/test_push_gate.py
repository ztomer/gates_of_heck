"""push_gate.sh — the pre-push gate runs on the pushed COMMIT, never on the working tree.

ZoneWM, 2026-09-21: a push's cold gate compiled the next round's uncommitted edits during its
test phase and certified a tree that was no commit at all. These tests build a throwaway repo
whose `tools/gate.sh` records what it saw, then push a commit while the working tree says
something else.
"""

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"

# A gate that reports the tree it ran in: the tracked marker, the ignored one, and its cwd.
GATE = """#!/usr/bin/env bash
set -euo pipefail
out="$GATE_REPORT"
printf 'tracked=%s\\n' "$(cat marker.txt)" >> "$out"
printf 'ignored=%s\\n' "$(cat local.cfg 2>/dev/null || echo ABSENT)" >> "$out"
printf 'cwd=%s\\n' "$PWD" >> "$out"
[ -f fail.flag ] && exit 1
exit 0
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp_path: Path, gatesrc: str = "") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(GATE)
    (repo / "marker.txt").write_text("COMMITTED")
    (repo / ".gitignore").write_text("local.cfg\n")
    (repo / ".gatesrc").write_text(gatesrc)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _push(repo: Path, sha: str, tmp_path: Path) -> tuple[int, str, str]:
    report = tmp_path / "report.txt"
    env = dict(os.environ, GATE_REPORT=str(report), GOH_DIR=str(REPO_ROOT))
    proc = subprocess.run(["bash", str(PUSH_GATE)], cwd=repo, env=env, text=True,
                          input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
                          capture_output=True)
    return proc.returncode, report.read_text() if report.exists() else "", proc.stdout + proc.stderr


def test_the_gate_sees_the_commit_not_the_working_tree(tmp_path):
    repo = _repo(tmp_path)
    sha = _git(repo, "rev-parse", "HEAD")
    (repo / "marker.txt").write_text("DIRTY")          # uncommitted edit, the incident's shape
    code, report, _ = _push(repo, sha, tmp_path)
    assert code == 0, report
    assert "tracked=COMMITTED" in report, report
    assert str(repo) not in report.split("cwd=")[1], "the gate ran inside the checkout"


def test_ignored_files_are_absent_unless_kept(tmp_path):
    repo = _repo(tmp_path)
    (repo / "local.cfg").write_text("LOCAL")
    sha = _git(repo, "rev-parse", "HEAD")
    code, report, _ = _push(repo, sha, tmp_path)
    assert code == 0 and "ignored=ABSENT" in report, report


def test_gatesrc_export_keep_carries_an_ignored_file(tmp_path):
    repo = _repo(tmp_path, gatesrc="GOH_EXPORT_KEEP='local.cfg'\n")
    (repo / "local.cfg").write_text("LOCAL")
    sha = _git(repo, "rev-parse", "HEAD")
    code, report, _ = _push(repo, sha, tmp_path)
    assert code == 0 and "ignored=LOCAL" in report, report


def test_a_red_gate_stops_the_push_and_leaves_no_worktree(tmp_path):
    repo = _repo(tmp_path)
    (repo / "fail.flag").write_text("")
    _git(repo, "add", "fail.flag")
    _git(repo, "commit", "-q", "-m", "red")
    sha = _git(repo, "rev-parse", "HEAD")
    code, _report, out = _push(repo, sha, tmp_path)
    assert code == 1, out
    assert "nothing pushed" in out
    assert _git(repo, "worktree", "list").count("\n") == 0, "the throwaway worktree was left behind"


def test_a_delete_is_not_gated(tmp_path):
    repo = _repo(tmp_path)
    code, report, out = _push(repo, "0" * 40, tmp_path)
    assert code == 0 and report == "" and "nothing to gate" in out


def test_the_stock_hook_delegates_to_push_gate():
    hook = (REPO_ROOT / "hooks" / "pre-push").read_text()
    assert "push_gate.sh" in hook and "gate.sh --full" not in hook, \
        "the hook must run the pushed commit through push_gate.sh, not the working tree through gate.sh"
