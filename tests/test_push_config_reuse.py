"""The push gate's floor rule: a push that changes only scanner floors re-runs the structural gate.

GOH.CFGPUSHSCOPE (owner, 2026-11-16). A consumer push whose diff was one `.gatesrc` floor line ran
the repo's whole push gate (scripts 1002 s, game_asset_factory 894 s at load 44-140). Now
gates/push_gate.sh asks lib/config_push.py: when the diff since the remote's tip is `.gatesrc`
scanner-floor lines only, and that tip has a green push verdict (lib/reuse.py's `pushed`, which
every green push records), the push runs the real `gates/structural.sh --full` -- the one reader
of a floor -- and reuses the tip's verdict for the rest. Anything else is the full gate, and the
push says why.
Each fixture is a consumer whose tools/gate.sh reports `full` to a file; the structural gate is
the real one, so a floor the tree cannot meet goes red on the short path too.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_reuse import recorded_hows, reuse
from timing_bounds import patience

PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"
ZERO = "0" * 40
FLOORS = "GOH_EMOJI_MIN_FILES=1\nGOH_CONFLICT_MARKERS_MIN_FILES=1\nGOH_SECRETS_MIN_FILES=1\n"
SHORT = "scanner floors only since"


def _consumer(repo: Path) -> Path:
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(
        '#!/usr/bin/env bash\nprintf "full\\n" >>"$GATE_REPORT"\n'
    )
    (repo / "src.py").write_text("A = 1\n")
    write_gatesrc(repo, FLOORS)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "the consumer")
    return repo


def _push(repo: Path, tmp_path: Path, remote_sha: str) -> tuple[int, list[str], str]:
    sha = git(repo, "rev-parse", "HEAD").strip()
    report = tmp_path / "report.txt"
    report.unlink(missing_ok=True)
    env = hermetic_env(
        drop_git=True, GATE_REPORT=str(report), GOH_REUSE_DIR=str(tmp_path / "reuse"),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"), GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
    )  # fmt: skip
    p = subprocess.run(
        ["bash", str(PUSH_GATE)], cwd=repo, env=env, text=True, capture_output=True,
        input=f"refs/heads/main {sha} refs/heads/main {remote_sha}\n", timeout=patience(300),
        check=False,
    )  # fmt: skip
    ran = report.read_text().split() if report.exists() else []
    return p.returncode, ran, p.stdout + p.stderr


def _pushed(tmp_path: Path) -> list[str]:
    """The shas the store's `pushed` path holds green (lib/reuse.py, its rows in goh-cache.db)."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("GOH_REUSE_DIR", str(tmp_path / "reuse"))
        recs = [rec for _, rec, _ in reuse.records("pushed")]
    return [r["sha"] for r in recs if r.get("verdict") == "green"]


def _gated_tip(repo: Path, tmp_path: Path) -> str:
    """The remote's tip: pushed through the full gate, which records it green."""
    rc, ran, out = _push(repo, tmp_path, ZERO)
    assert rc == 0 and ran == ["full"], (ran, out)
    return git(repo, "rev-parse", "HEAD").strip()


def _floor(repo: Path, old: str, new: str, *touch: str) -> str:
    rc = repo / ".gatesrc"
    rc.write_text(rc.read_text().replace(old, new))
    for rel in touch:
        with (repo / rel).open("a") as f:
            f.write("# touched\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "gate: declare a floor")
    return git(repo, "rev-parse", "HEAD").strip()


def _full(ran: list[str], out: str, reason: str) -> None:
    assert ran == ["full"], (ran, out)
    assert f"pre-push: the full gate: {reason}" in out, out


def test_a_floor_only_commit_on_a_push_gated_tip_runs_the_structural_gate_only(
    repo, tmp_path
) -> None:
    tip = _gated_tip(_consumer(repo), tmp_path)
    assert _pushed(tmp_path) == [tip]
    sha = _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2  # read 3\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0, out
    assert ran == [], (ran, out)  # tools/gate.sh never ran: the tip's verdict stands for it
    assert f"{SHORT} {tip[:8]}" in out and "all structural gates passed" in out, out
    # Recorded like any green push, so the next floor push chains on it.
    assert sha in _pushed(tmp_path), out
    assert recorded_hows(tmp_path / "reuse", sha) == ["config"], out  # its gate: the floor rule


def test_a_floor_the_tree_cannot_meet_is_red_on_the_short_path(repo, tmp_path) -> None:
    tip = _gated_tip(_consumer(repo), tmp_path)
    sha = _floor(repo, "GOH_SECRETS_MIN_FILES=1\n", "GOH_SECRETS_MIN_FILES=999\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc != 0 and ran == [], (ran, out)
    assert f"{SHORT} {tip[:8]}" in out and "GOH_SECRETS_MIN_FILES" in out, out
    assert sha not in _pushed(tmp_path), out


def test_a_floor_plus_a_code_change_takes_the_full_gate(repo, tmp_path) -> None:
    tip = _gated_tip(_consumer(repo), tmp_path)
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2\n", "src.py")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0, out
    _full(ran, out, "src.py is not .gatesrc")


def test_a_tip_with_no_recorded_push_verdict_takes_the_full_gate(repo, tmp_path) -> None:
    _consumer(repo)
    tip = git(
        repo, "rev-parse", "HEAD"
    ).strip()  # on the remote, but no gate of this push judged it
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0, out
    _full(ran, out, f"the remote's {tip[:8]} has no green push verdict")


def test_a_tip_whose_push_was_red_takes_the_full_gate(repo, tmp_path) -> None:
    _consumer(repo)
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 3\n")
    git(repo, "commit", "-q", "--no-verify", "-am", "a red gate")
    tip = git(repo, "rev-parse", "HEAD").strip()
    rc, _, out = _push(repo, tmp_path, ZERO)
    assert rc != 0, out
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2\n")
    _, _, out = _push(repo, tmp_path, tip)
    assert f"pre-push: the full gate: the remote's {tip[:8]} has no green push verdict" in out, out


def test_a_key_the_gate_reads_takes_the_full_gate(repo, tmp_path) -> None:
    """GOH_MAX_LINES is no floor; nor is a floor spelled with a substitution the shell would run."""
    tip = _gated_tip(_consumer(repo), tmp_path)
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=1\nGOH_MAX_LINES=900\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0, out
    _full(ran, out, "`GOH_MAX_LINES=900` is not a scanner floor line")
    git(repo, "reset", "-q", "--hard", tip)
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=$(echo 2)\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    _full(ran, out, "`GOH_EMOJI_MIN_FILES=$(echo 2)` is not a scanner floor line")


def test_a_file_that_names_the_changed_floor_takes_the_full_gate(repo, tmp_path) -> None:
    _consumer(repo)
    (repo / "check_floor.py").write_text('KEY = "GOH_EMOJI_MIN_FILES"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "a reader of the floor")
    tip = _gated_tip(repo, tmp_path)
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0, out
    _full(ran, out, "check_floor.py names GOH_EMOJI_MIN_FILES, so it may read it")


def test_the_floor_keys_are_the_scanners_own() -> None:
    """The list is read from crate::subject, never copied: every Subject's key, and only those."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "lib"))
    import config_push

    src = (REPO_ROOT / config_push.SUBJECTS).read_text(encoding="utf-8")
    n = int(src.split("pub const ALL: [&Subject; ", 1)[1].split("]", 1)[0])
    keys = config_push.floor_keys(REPO_ROOT)
    assert len(keys) == n and "GOH_DEAD_PORT_MIN_FILES" in keys, sorted(keys)
    assert "GOH_MAX_LINES" not in keys and "GOH_JS_SYNTAX" not in keys
