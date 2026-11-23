"""tools/landq.py's batch (GOH.LANDBATCH): several pending lands of one repo, ONE land gate.

The queue gated one land at a time, each rebased on main and gated alone, so with three lanes a lane
waited about as long in the queue as it spent gated (2026-11-17: 572 agent-minutes queued against
711 gated). Merge-queue style, the lander now stacks up to GOH_LANDQ_BATCH (default 3) pending land
requests of one repo -- each rebased on the one before it, in queue order -- and runs land.sh once,
on the stack's tip. What must hold: a green stack lands every item, each done record naming ITS
commit; a red stack bisects, so one bad item never fails the others and the bad one is red with its
own log; an item that conflicts with the stack below it is red (conflict) and the rest go on; and
GOH_LANDQ_BATCH=1 is the old one-by-one queue. The gate here is GOH_LAND_GATE: it logs each run's
tip subject (so a test counts gate runs and reads what each judged) and fails on a planted file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, git_ok, hermetic_env, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
from timing_bounds import patience

LANDQ = REPO_ROOT / "tools" / "landq.py"
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""
# One line per gate run: the subject of the commit it judged; red when the tree holds bad.txt.
GATE = (
    'git log -1 --format=%s >>"$GATE_LOG"; '
    "if [ -e bad.txt ]; then echo planted-red-in-$(git log -1 --format=%s); exit 1; fi"
)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
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
    )


def landq(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(LANDQ), *args]
    return subprocess.run(argv, env=env, capture_output=True, check=False,
                          stdin=subprocess.DEVNULL, text=True, timeout=patience(300))  # fmt: skip


def lane(repo: Path, tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """A lane's worktree on branch NAME, one commit (subject NAME) writing FILES, enqueued."""
    wt = tmp_path / f"wt-{name}"
    git(repo, "worktree", "add", "-q", "-b", name, str(wt))
    for rel, text in files.items():
        write(wt, rel, text)
    git(wt, "add", "-A")
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", name)
    return wt


def enqueue_all(env: dict[str, str], wts: list[tuple[Path, str]]) -> None:
    for wt, item in wts:
        # a trivial pre-step, never `--no-pre`: a `--no-pre` land is judged alone (GOH.NOPREBATCH)
        pre = {**env, "GOH_LAND_PRE": "true"}
        done = landq(pre, "--enqueue", "--foreground", "--land", str(wt), "--item", item,
                     "--no-start")  # fmt: skip
        assert done.returncode == 0, done.stdout + done.stderr


def recorded(env: dict[str, str]) -> dict[str, dict]:
    out = {}
    log = Path(env["FAKE_LOOPCTL_LOG"])
    for line in log.read_text().splitlines() if log.exists() else []:
        argv = json.loads(line)
        if argv[:1] == ["set"] and argv[2] == "landing":
            out[argv[1]] = json.loads(argv[3])
    return out


def gate_runs(env: dict[str, str]) -> list[str]:
    log = Path(env["GATE_LOG"])
    return log.read_text().split() if log.exists() else []


def report(env: dict[str, str]) -> dict:
    done = landq(env, "--report", "--json")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def on_main(repo: Path, sha: str) -> bool:
    return git_ok(repo, "merge-base", "--is-ancestor", sha, "HEAD")


def three(repo: Path, tmp_path: Path, middle: dict[str, str]) -> list[tuple[Path, str]]:
    return [
        (lane(repo, tmp_path, "one", {"one.txt": "1\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", middle), "T.TWO"),
        (lane(repo, tmp_path, "three", {"three.txt": "3\n"}), "T.THREE"),
    ]


@pytest.mark.load_sensitive  # --wait is bounded
def test_three_pending_lands_land_in_one_gate_run_each_naming_its_own_sha(repo, tmp_path, env):
    wts = three(repo, tmp_path, {"two.txt": "2\n"})
    enqueue_all(env, wts)
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    assert gate_runs(env) == ["three"], gate_runs(env)  # ONE gate, on the stack's tip
    got = recorded(env)
    shas = [got[item]["sha"] for _, item in wts]
    assert all(got[item]["state"] == "landed" for _, item in wts), got
    assert len(set(shas)) == 3, shas  # each record names its OWN commit
    assert [subject(repo, s) for s in shas] == ["one", "two", "three"], shas
    assert all(on_main(repo, s) for s in shas) and git(repo, "rev-parse", "HEAD").strip() == shas[2]
    assert all(got[item]["batch"]["gates"] == 1 for _, item in wts), got
    for _, item in wts:  # --wait returns per item
        waited = landq(env, "--wait", item, "--timeout", str(patience(30)))
        assert waited.returncode == 0, waited.stdout + waited.stderr
    tot = report(env)["totals"]  # one gate run for three landed, not three
    assert (tot["landed"], tot["gates"], tot["gates_per_landed"]) == (3, 1, 0.33), tot


# GATE, but the first half's run alone on main (one.txt, nothing stacked on it) waits (bounded) on a
# fifo for the speculative second half's run on main (bad.txt, no one.txt) to have gated. Else a
# slow runner's speculative land.sh was still before its gate when the first half landed, so it was
# stopped unlogged: the batch counts every run it started, the log one fewer (CI, v0.136.0).
MEET = (
    'git log -1 --format=%s >>"$GATE_LOG"; '
    'if [ -e bad.txt ] && [ ! -e one.txt ]; then echo spec >"$BARRIER"; fi; '
    "if [ -e one.txt ] && [ ! -e two.txt ] && [ ! -e three.txt ]; then "
    'read -r -t "$WAIT" _ <"$BARRIER"; fi; '
    "if [ -e bad.txt ]; then echo planted-red-in-$(git log -1 --format=%s); exit 1; fi"
)


@pytest.fixture
def meet(env: dict[str, str], tmp_path: Path):
    barrier = tmp_path / "barrier"
    os.mkfifo(barrier)
    held = os.open(barrier, os.O_RDWR)  # a reader and a writer: no open blocks, no line lost
    yield {**env, "GOH_LAND_GATE": MEET, "BARRIER": str(barrier), "WAIT": str(int(patience(60)))}
    os.close(held)


@pytest.mark.load_sensitive  # --wait is bounded
def test_a_red_middle_item_bisects_out_and_the_others_land(repo, tmp_path, meet):
    env = meet
    wts = three(repo, tmp_path, {"two.txt": "2\n", "bad.txt": "planted\n"})
    enqueue_all(env, wts)
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.THREE"]["state"] == "landed", got
    assert got["T.TWO"]["state"] == "red", got
    assert on_main(repo, got["T.ONE"]["sha"]) and on_main(repo, got["T.THREE"]["sha"])
    assert not (repo / "bad.txt").exists() and (repo / "three.txt").exists()
    own = Path(got["T.TWO"]["log"])  # its own log, saying why it is red
    assert own.name.endswith("T.TWO.log") and "planted-red-in-two" in own.read_text(), own
    assert str(own) not in (got["T.ONE"]["log"], got["T.THREE"]["log"])
    # every red run its stack was in, the first (all three stacked) included: the selection-miss
    # ledger reads their failed tests (GOH.SELECTLOCKORDER); a landed record names none
    reds = got["T.TWO"]["red_logs"]
    assert reds[0].endswith(".gate1.log") and len(reds) >= 2, reds
    assert "red_logs" not in got["T.ONE"] and "red_logs" not in got["T.THREE"], got
    runs = gate_runs(env)
    # The stack, then the bisection: serial it was <= 4 runs in 4 rounds; with each red split's
    # second half gated on main beside the first (GOH.LANDBISECTPAR) it is <= 5 runs in 3 rounds.
    assert runs[0] == "three" and len(runs) <= 5, runs
    assert all(got[i]["batch"]["gates"] == len(runs) for i in ("T.ONE", "T.TWO", "T.THREE")), got
    waited = landq(env, "--wait", "T.TWO", "--timeout", str(patience(30)))
    assert waited.returncode == 1 and "planted-red-in-two" in waited.stderr, waited.stderr
    enqueued = git(wts[1][0], "rev-parse", "HEAD").strip()  # the red branch is the lane's again
    assert enqueued == got["T.TWO"]["sha"] and subject(repo, f"{enqueued}~1") != "one", enqueued
    assert report(env)["totals"]["gates"] == len(runs)


def test_a_conflicting_item_is_red_conflict_and_does_not_block_the_rest(repo, tmp_path, env):
    wts = [
        (lane(repo, tmp_path, "one", {"shared.txt": "from one\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"shared.txt": "from two\n"}), "T.TWO"),
        (lane(repo, tmp_path, "three", {"three.txt": "3\n"}), "T.THREE"),
    ]
    enqueue_all(env, wts)
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.THREE"]["state"] == "landed", got
    assert got["T.TWO"]["state"] == "red" and "conflict" in got["T.TWO"]["why"], got
    assert "conflicts with main" in Path(got["T.TWO"]["log"]).read_text()
    assert gate_runs(env) == ["three"], gate_runs(env)  # one and three, stacked: one gate
    assert (repo / "shared.txt").read_text() == "from one\n"


def test_batch_of_one_is_the_one_by_one_queue(repo, tmp_path, env):
    wts = three(repo, tmp_path, {"two.txt": "2\n"})
    env = {**env, "GOH_LANDQ_BATCH": "1"}
    enqueue_all(env, wts)
    assert landq(env, "--drain").returncode == 0
    assert gate_runs(env) == ["one", "two", "three"], gate_runs(env)  # each alone, in queue order
    got = recorded(env)
    assert all(got[i]["state"] == "landed" and "batch" not in got[i] for _, i in wts), got


def test_a_stack_whose_gate_never_got_a_slot_is_not_bisected(repo, tmp_path, env):
    """GOH.PREWAITVERDICT: land.sh exits 75 when its gate's slot wait expired. Nothing was judged,
    so a red verdict that bisects (a slot wait per half) would be a lie: one gate run, no split."""
    from test_gate_slot import Gate
    from test_one_admission import _end, _env, _until

    wts = three(repo, tmp_path, {"two.txt": "2\n"})
    enqueue_all(env, wts)
    holder = Gate(tmp_path, "push-holder", _env(tmp_path, GOH_GATE_SLOTS="1"))
    _until(lambda: "TAKEN" in holder.out() or holder.proc.poll() is not None)
    slots = _env(tmp_path, GOH_GATE_SLOTS="1", GOH_LAND_GATE_WAIT="1s")
    try:
        done = landq({**env, **{k: v for k, v in slots.items() if k.startswith("GOH_G")},
                      "GOH_LOAD_DIR": slots["GOH_LOAD_DIR"], "GOH_MEM_ADMIT": "0",
                      "GOH_LAND_GATE_WAIT": "1s"}, "--drain")  # fmt: skip
    finally:
        _end(holder)
    assert done.returncode == 0, done.stdout + done.stderr
    got = recorded(env)
    assert gate_runs(env) == [], "the gate ran"
    for _, item in wts:
        assert got[item]["state"] == "red" and "gate never ran" in got[item]["why"], got
        assert got[item]["batch"]["gates"] == 1, got  # the stack's one run; no bisection
        assert "the gate never ran" in Path(got[item]["log"]).read_text()
