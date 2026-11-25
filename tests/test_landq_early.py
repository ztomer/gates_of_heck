"""The landing queue's early conflict check (GOH.EARLYCONFLICT, lib/landq_early.py).

A conflict was found only when the item reached the head of the queue: on 2026-11-18 GOH.CRATEFLOOR
waited ~40 min behind GOH.LANDTARGET's land, then went red in 0 s on a conflict with it. What must
hold: when a land moves main, a pending land that no longer stacks is red at once -- before the
requests queued ahead of it run -- with the batch's record (`conflict: did not stack on`), a log
naming the files and the landing; one that stacks still lands; an enqueue that conflicts is red at
once and never queued. Nothing is gated and no lane's worktree is moved to find it.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
import test_landq_batch as batch
from conftest import REPO_ROOT, git, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
import landq_early
import landq_state
from timing_bounds import patience

env = batch.env  # the queue fixture (a fake loopctl, a planted-file gate), by its own name
enqueue_all, gate_runs, landq, lane, recorded = (
    batch.enqueue_all,
    batch.gate_runs,
    batch.landq,
    batch.lane,
    batch.recorded,
)


def order(env: dict[str, str]) -> list[str]:
    """The items in the order the lander recorded them."""
    log = Path(env["FAKE_LOOPCTL_LOG"]).read_text().splitlines()
    return [json.loads(line)[1] for line in log if json.loads(line)[:1] == ["set"]]


@pytest.mark.load_sensitive  # --wait is bounded
def test_a_pending_land_that_conflicts_with_a_landing_is_red_before_its_turn(repo, tmp_path, env):
    env = {**env, "GOH_LANDQ_BATCH": "1"}  # one land at a time: the conflict is NOT the stack's
    wts = [
        (lane(repo, tmp_path, "one", {"shared.txt": "from one\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO"),
        (lane(repo, tmp_path, "three", {"shared.txt": "from three\n"}), "T.THREE"),
    ]
    enqueued = git(wts[2][0], "rev-parse", "HEAD").strip()
    enqueue_all(env, wts, told="T.THREE")
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.TWO"]["state"] == "landed", got
    assert got["T.THREE"]["state"] == "red", got
    assert got["T.THREE"]["why"].startswith("conflict: did not stack on"), got["T.THREE"]
    # red the moment ONE landed: before T.TWO, which was queued ahead of it, ran
    assert order(env) == ["T.THREE", "T.ONE", "T.THREE", "T.TWO"], order(env)  # the first: told
    assert gate_runs(env) == ["one", "two"], gate_runs(env)  # never gated
    log = Path(got["T.THREE"]["log"]).read_text()
    assert "conflicts with main" in log and "shared.txt" in log, log
    assert " one\n" in log, log  # the landing that caused it, by its subject
    assert git(wts[2][0], "rev-parse", "HEAD").strip() == enqueued  # its worktree untouched
    waited = landq(env, "--wait", "T.THREE", "--timeout", str(patience(30)))
    assert waited.returncode == 1 and "conflicts with main" in waited.stderr, waited.stderr


@pytest.mark.load_sensitive  # --wait is bounded
def test_an_enqueue_that_conflicts_with_main_is_red_at_once_and_never_queued(repo, tmp_path, env):
    wt = lane(repo, tmp_path, "late", {"shared.txt": "from late\n"})
    write(repo, "shared.txt", "landed meanwhile\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m",
        "T.EARLIER landed")  # fmt: skip
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.LATE", "--no-start", "--no-pre",
                 "fixture: the pre-step is not under test")  # fmt: skip
    assert done.returncode == 1 and "conflicts with main" in done.stderr, done.stdout + done.stderr
    q = Path(env["GOH_LANDQ_DIR"])
    assert not landq_state.listing(q, "pending")
    assert recorded(env)["T.LATE"]["why"].startswith("conflict: did not stack on")
    waited = landq(env, "--wait", "T.LATE", "--timeout", str(patience(30)))
    assert waited.returncode == 1 and "T.EARLIER landed" in waited.stderr, waited.stderr


@pytest.mark.load_sensitive  # it times the check
def test_only_a_land_that_conflicts_with_main_is_red_and_the_cost_per_item(repo, tmp_path, env):
    """A conflict with a request still PENDING is no verdict (it may be red); one with main is."""
    reqs = []
    for name, files in (("a", {"a.txt": "a\n"}), ("b", {"b.txt": "b\n"}), ("c", {"a.txt": "c\n"})):
        wt = lane(repo, tmp_path, name, files)
        sha = git(wt, "rev-parse", "HEAD").strip()
        reqs.append((tmp_path / f"{name}.json", {"sha": sha, "repo": str(repo), "item": name}))
    assert landq_early.judge(repo, reqs) == []  # c conflicts only with a, which has not landed
    git(repo, "merge", "-q", "--ff-only", reqs[0][1]["sha"])  # a lands
    start = time.perf_counter()
    reds = landq_early.judge(repo, reqs[1:])
    per_item = (time.perf_counter() - start) / len(reqs[1:])
    assert [p.name for p, _, _ in reds] == ["c.json"], reds
    assert "a.txt" in reds[0][2] and " a\n" in reds[0][2], reds[0][2]
    assert per_item < patience(2.0), per_item
    print(f"early conflict check: {per_item * 1000:.0f} ms per pending item")


def test_a_re_enqueue_whose_partial_landed_stacks_as_the_rebase_does(repo, tmp_path):
    """GOH.REENQUEUEREBASE: GOH-RUFFBUMPITEM's re-enqueue (2026-11-22, aabaee7e) ran a 1411 s
    pre-step in a slot whose rebase onto main was CLEAN, then went red `conflict: did not stack
    on 06d306bf`: 10 of its 15 commits had landed already, and the replay merged each again onto
    a main that had moved past them. A rebase drops a commit whose patch is upstream; so does
    the replay (`--cherry-pick`), so the check refuses only what the land's rebase would."""
    wt = lane(repo, tmp_path, "tiny", {"shared.txt": "fix 1\n"})
    write(wt, "shared.txt", "fix 1\nfix 2\n")
    git(wt, "add", "-A")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "2")
    for sha in git(wt, "rev-list", "--reverse", "HEAD~2..HEAD").split():  # the partial lands
        git(repo, "-c", "user.name=l", "-c", "user.email=l@l", "cherry-pick", sha)
    write(wt, "three.txt", "fix 3\n")
    git(wt, "add", "-A")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "3")
    req = {"sha": git(wt, "rev-parse", "HEAD").strip(), "repo": str(repo), "item": "T.TINY"}
    assert landq_early.judge(repo, [(tmp_path / "t.json", req)]) == []
    write(repo, "three.txt", "landed meanwhile\n")  # a real conflict is still one
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=l", "-c", "user.email=l@l", "commit", "-q", "--no-verify", "-m", "x")
    (red,) = landq_early.judge(repo, [(tmp_path / "t.json", req)])
    assert "three.txt" in red[2] and "shared.txt" not in red[2], red[2]


def test_an_enqueue_that_does_not_stack_is_red_before_its_guards_and_pre_step(repo, tmp_path, env):
    """GOH.BATCHCONFLICTFIRST (a): the stacking check needs no test, so it runs first: neither
    the enqueue's guards nor its pre-step start for a tip that conflicts with main."""
    wt = lane(repo, tmp_path, "late", {"shared.txt": "from late\n"})
    write(repo, "shared.txt", "landed meanwhile\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m",
        "T.EARLIER landed")  # fmt: skip
    env = {**env, "GOH_LAND_PRE": "true", "GOH_LAND_CHECK_STEPS": "true"}
    done = landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.LATE",
                 "--no-start")  # fmt: skip
    out = done.stdout + done.stderr
    assert done.returncode == 1 and "conflicts with main" in out, out
    assert "check: true" not in out and "pre-step before queueing" not in out, out
    assert recorded(env)["T.LATE"]["why"].startswith("conflict: did not stack on"), out
    assert not landq_state.listing(Path(env["GOH_LANDQ_DIR"]), "pending")
