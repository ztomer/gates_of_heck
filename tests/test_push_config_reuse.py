"""The push gate's floor rule: a push that changes only scanner floors re-runs the structural gate.

GOH.CFGPUSHSCOPE (owner, 2026-11-16). A consumer push whose diff was one `.gatesrc` floor line ran
the repo's whole push gate (scripts 1002 s, game_asset_factory 894 s at load 44-140). Now
gates/push_gate.sh asks lib/config_push.py: when the diff since the remote's tip is `.gatesrc`
scanner-floor lines only, and that tip has a green push verdict (lib/reuse.py's `pushed`, which
every green push records), the push runs the real `gates/structural.sh --full` -- the one reader
of a floor -- and reuses the tip's verdict for the rest. Anything else is the full gate, and the
push says why.
Each fixture is a consumer whose tools/gate.sh reports `full` to a file; the structural gate is
the real one, so a floor the tree cannot meet goes red on the short path too. The refusals are
config_push.decide's own, asked in process.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_reuse import recorded_hows, reuse
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import config_push

PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"
ZERO = "0" * 40
FLOORS = "GOH_EMOJI_MIN_FILES=1\nGOH_CONFLICT_MARKERS_MIN_FILES=1\nGOH_SECRETS_MIN_FILES=1\n"
SHORT = "scanner floors only since"


FULL_GATE = '#!/usr/bin/env bash\nprintf "full\\n" >>"$GATE_REPORT"\n'


def _consumer(repo: Path) -> Path:
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(FULL_GATE)
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
    # ... and the next, a floor the tree cannot meet, is red on the short path too.
    red = _floor(repo, "GOH_SECRETS_MIN_FILES=1\n", "GOH_SECRETS_MIN_FILES=999\n")
    rc, ran, out = _push(repo, tmp_path, sha)
    assert rc != 0 and ran == [], (ran, out)
    assert f"{SHORT} {sha[:8]}" in out and "GOH_SECRETS_MIN_FILES" in out, out
    assert red not in _pushed(tmp_path), out


def test_a_tip_whose_push_was_red_takes_the_full_gate(repo, tmp_path) -> None:
    """A refusal through the push gate, its reason said; every other reason is `decide`'s, below."""
    _consumer(repo)
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 3\n")
    git(repo, "commit", "-q", "--no-verify", "-am", "a red gate")
    tip = git(repo, "rev-parse", "HEAD").strip()
    rc, _, out = _push(repo, tmp_path, ZERO)
    assert rc != 0 and _pushed(tmp_path) == [], out
    (repo / "tools" / "gate.sh").write_text(FULL_GATE)
    _floor(repo, "GOH_EMOJI_MIN_FILES=1\n", "GOH_EMOJI_MIN_FILES=2\n")
    rc, ran, out = _push(repo, tmp_path, tip)
    assert rc == 0 and ran == ["full"], (ran, out)
    reason = f"the remote's {tip[:8]} has no green push verdict"
    assert f"pre-push: the full gate: {reason}" in out, out


def test_every_other_change_takes_the_full_gate_for_its_reason(repo, monkeypatch) -> None:
    """`decide`, asked of one repository: the store is the one question stubbed, that a red or
    unmade push left nothing there is the push gate's, above. GOH_MAX_LINES is no floor; nor is a
    floor spelled with a substitution the shell would run."""
    _consumer(repo)
    (repo / "check_floor.py").write_text('KEY = "GOH_SECRETS_MIN_FILES"\n')
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "a reader of one floor")
    tip = git(repo, "rev-parse", "HEAD").strip()
    green: set[str] = {tip}
    monkeypatch.setattr(config_push.reuse, "pushed", lambda _root, sha: sha in green)
    two = "GOH_EMOJI_MIN_FILES=2\n"

    def decided(old: str, new: str, *touch: str) -> tuple[str, str]:
        _floor(repo, old, new, *touch)
        got = config_push.decide(repo, tip, REPO_ROOT)
        git(repo, "checkout", "-q", "--detach", tip)
        return got

    emoji, secrets = "GOH_EMOJI_MIN_FILES=1\n", "GOH_SECRETS_MIN_FILES=1\n"
    assert decided(emoji, two) == ("config", tip)  # the control: the same commit, allowed
    for got, reason in (
        (decided(emoji, two, "src.py"), "src.py is not .gatesrc"),
        (
            decided(emoji, emoji + "GOH_MAX_LINES=900\n"),
            "`GOH_MAX_LINES=900` is not a scanner floor",
        ),
        (
            decided(emoji, "GOH_EMOJI_MIN_FILES=$(echo 2)\n"),
            "`GOH_EMOJI_MIN_FILES=$(echo 2)` is not a",
        ),
        (
            decided(secrets, "GOH_SECRETS_MIN_FILES=2\n"),
            "check_floor.py names GOH_SECRETS_MIN_FILES,",
        ),
    ):
        assert got[0] == "full" and reason in got[1], got
    green.clear()
    assert decided(emoji, two)[1].startswith(f"the remote's {tip[:8]} has no green push verdict")


def test_the_floor_keys_are_the_scanners_own() -> None:
    """The list is read from crate::subject, never copied: every Subject's key, and only those."""
    src = (REPO_ROOT / config_push.SUBJECTS).read_text(encoding="utf-8")
    n = int(src.split("pub const ALL: [&Subject; ", 1)[1].split("]", 1)[0])
    keys = config_push.floor_keys(REPO_ROOT)
    assert len(keys) == n and "GOH_DEAD_PORT_MIN_FILES" in keys, sorted(keys)
    assert "GOH_MAX_LINES" not in keys and "GOH_JS_SYNTAX" not in keys
