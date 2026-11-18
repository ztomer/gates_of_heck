"""lib/land_slot.py: a land judges the commit, never the lane's working tree (GOH.LANDSNAPSHOT).

The enqueue's pre-step, and the lander's pre-step, stack and gate, ran in the lane's own worktree.
On 2026-11-18 a lane's edit during a pre-step may have reached its verdict, two items of one
worktree met on its suite lock, and the lander rebased a worktree whose second item was queued, so
that item was refused, "tip moved after enqueue". Now every run of a request is in a slot, a
detached checkout of the enqueued sha under the queue's state. What must hold: an edit in the lane
during the land changes no verdict; the lander never moves the lane's tip; two items enqueued from
one worktree both land; and the slots are released and reused, the checkout's seconds recorded.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_slot
from timing_bounds import patience

LANDQ = REPO_ROOT / "tools" / "landq.py"
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""
# The gate reads the tree it runs in: red on an untracked bad.txt or a "bad" line in f.txt.
GATE = 'pwd -P >>"$GATE_LOG"; ! [ -e bad.txt ] && ! grep -q bad f.txt'


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
        GOH_LAND_TIMEOUT="120",
        GOH_LAND_GATE=GATE,
        GATE_LOG=str(tmp_path / "gate.log"),
        **extra,
    )


def landq(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(LANDQ), *args]
    return subprocess.run(argv, env=env, capture_output=True, check=False,
                          stdin=subprocess.DEVNULL, text=True, timeout=patience(300))  # fmt: skip


def commit(wt: Path, name: str, text: str) -> str:
    write(wt, "f.txt", text)
    git(wt, "add", "-A")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", name)
    return git(wt, "rev-parse", "HEAD").strip()


def lane(repo: Path, tmp_path: Path) -> tuple[Path, str]:
    wt = tmp_path / "wt-lane"
    git(repo, "worktree", "add", "-q", "-b", "lane", str(wt))
    return wt, commit(wt, "first", "first\n")


def enqueue(env: dict[str, str], wt: Path, item: str, pre: bool = False) -> None:
    flags = () if pre else ("--no-pre", "fixture: the pre-step is not under test")
    done = landq(env, "--enqueue", "--land", str(wt), "--item", item, "--no-start", *flags)
    assert done.returncode == 0, done.stdout + done.stderr


def recorded(env: dict[str, str]) -> dict[str, dict]:
    out = {}
    log = Path(env["FAKE_LOOPCTL_LOG"])
    for line in log.read_text().splitlines() if log.exists() else []:
        argv = json.loads(line)
        if argv[:1] == ["set"] and argv[2] == "landing":
            out[argv[1]] = json.loads(argv[3])
    return out


def test_an_edit_in_the_lane_during_the_land_does_not_change_the_verdict(repo, tmp_path):
    """The seeded edit: after the enqueue the lane writes bad.txt and a "bad" f.txt, neither
    committed. The gate reads its tree, so in the lane it went red (or land.sh refused the dirty
    tree); in the slot it judges the enqueued commit, green."""
    env = _env(tmp_path)
    wt, tip = lane(repo, tmp_path)
    enqueue(env, wt, "T.EDIT")
    write(wt, "bad.txt", "seeded\n")
    write(wt, "f.txt", "bad\n")
    landq(env, "--drain")
    got = recorded(env)["T.EDIT"]
    assert got["state"] == "landed" and got["sha"] == tip, got
    ran = Path(env["GATE_LOG"]).read_text().split()
    assert ran and str(wt.resolve()) not in ran, ran  # the gate never ran in the lane
    assert (wt / "f.txt").read_text() == "bad\n"  # and the lane's edit is the lane's
    assert "land lane @" in Path(got["log"]).read_text()  # named by the lane's branch, not HEAD


def test_the_lander_never_moves_the_lanes_tip(repo, tmp_path):
    """Main moved, so the land rebases: in the slot. The lane's HEAD and reflog are untouched."""
    env = _env(tmp_path)
    wt, tip = lane(repo, tmp_path)
    write(repo, "m.txt", "main moved\n")
    git(repo, "add", "m.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "m")
    reflog = git(wt, "reflog", "show", "--format=%H %gs", "HEAD")
    enqueue(env, wt, "T.TIP")
    landq(env, "--drain")
    got = recorded(env)["T.TIP"]
    assert got["state"] == "landed" and got["sha"] != tip and got["enqueued"] == tip, got
    assert git(repo, "rev-parse", "HEAD").strip() == got["sha"]
    assert git(wt, "rev-parse", "HEAD").strip() == tip, "the lander moved the lane's tip"
    assert git(wt, "reflog", "show", "--format=%H %gs", "HEAD") == reflog


def test_two_items_enqueued_from_one_worktree_both_land(repo, tmp_path):
    """Item one is enqueued, the lane commits item two and enqueues it: both land, in one batch.
    Each pre-step ran in its own checkout, neither the lane: no suite lock is shared."""
    seen = tmp_path / "pre.log"
    env = _env(tmp_path, GOH_LAND_PRE=f"pwd -P >>{seen}")
    wt, one = lane(repo, tmp_path)
    enqueue(env, wt, "T.ONE", pre=True)
    two = commit(wt, "second", "second\n")
    enqueue(env, wt, "T.TWO", pre=True)
    landq(env, "--drain")
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.TWO"]["state"] == "landed", got
    assert got["T.ONE"]["sha"] == one and got["T.TWO"]["sha"] == two, got
    assert git(repo, "rev-parse", "HEAD").strip() == two
    assert got["T.ONE"]["batch"] == got["T.TWO"]["batch"], got  # one batch, one gate
    assert len(Path(env["GATE_LOG"]).read_text().split()) == 1
    dirs = seen.read_text().split()
    enqueues = dirs[:2]
    assert len(set(enqueues)) == 2 and str(wt.resolve()) not in dirs, dirs


def test_a_slot_is_released_after_its_land_and_its_checkout_seconds_are_recorded(repo, tmp_path):
    env = _env(tmp_path)
    wt, _ = lane(repo, tmp_path)
    enqueue(env, wt, "T.REL")
    landq(env, "--drain")
    got = recorded(env)["T.REL"]
    assert got["state"] == "landed" and isinstance(got.get("slot_s"), float), got
    base = land_slot.root(tmp_path / "landq", repo)
    assert not list(base.glob("*.owner")), "the slot was never released"
    commit(wt, "again", "again\n")
    enqueue(env, wt, "T.AGAIN")  # the same slot, reused
    (req,) = [json.loads(p.read_text()) for p in (tmp_path / "landq" / "pending").glob("*.json")]
    assert Path(req["worktree"]) == base / "0", req
    assert Path(req["lane"]).resolve() == wt.resolve(), req


def test_claims_never_share_a_slot_and_a_stale_owner_is_reclaimed(repo, tmp_path):
    q = tmp_path / "q"
    sha = git(repo, "rev-parse", "HEAD").strip()
    a = land_slot.claim(q, repo, sha)
    b = land_slot.claim(q, repo, sha)
    assert a["path"] != b["path"], (a, b)
    assert git(Path(b["path"]), "rev-parse", "HEAD").strip() == sha
    land_slot.release(q, repo, a["path"], a["token"])
    assert land_slot.claim(q, repo, sha)["path"] == a["path"]  # free again: reused
    owner = Path(b["path"]).with_name("1.owner")
    owner.write_text(json.dumps({"pid": 2**22, "token": "x"}))  # past any pid: nobody alive
    assert land_slot.claim(q, repo, sha)["path"] == b["path"]  # nobody alive, nothing queued


def test_a_slot_past_keep_is_removed_when_released(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(land_slot, "KEEP", 1)
    q, sha = tmp_path / "q", git(repo, "rev-parse", "HEAD").strip()
    kept, extra = land_slot.claim(q, repo, sha), land_slot.claim(q, repo, sha)
    land_slot.release(q, repo, extra["path"], extra["token"])
    land_slot.release(q, repo, kept["path"], kept["token"])
    assert not Path(extra["path"]).exists() and Path(kept["path"]).is_dir()
    listed = git(repo, "worktree", "list", "--porcelain")
    assert extra["path"] not in listed and kept["path"] in listed
