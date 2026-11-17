"""tools/landq.py (`goh land`): the landing queue (GOH.LANDQUEUE).

An agent ends at "committed and enqueued"; ONE lander drains the requests in order, runs each
repo's push (its pre-push gate) or its ship, and records the outcome on the item in the loop queue.
What must hold with nobody watching: two requests run one after the other, never together; a green
push lands exactly the enqueued sha and records `landed`; a red gate pushes nothing and records the
log that says why; a request a dead lander was running is run again, not lost. The loopctl here is
a fake that records its argv.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

sys.path.insert(0, str(REPO_ROOT / "lib"))
import source_closure as sc
from timing_bounds import GiveUp, patience

LANDQ = REPO_ROOT / "tools" / "landq.py"
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
"""
# A pre-push hook standing in for the push gate: it records when it ran, then passes or fails.
HOOK = """\
#!/bin/sh
echo "start $(basename "$PWD") $(python3 -c 'import time; print(time.time())')" >>"$HOOK_LOG"
sleep 0.4
echo "gate output for $(basename "$PWD")"
echo "end $(basename "$PWD") $(python3 -c 'import time; print(time.time())')" >>"$HOOK_LOG"
exit {rc}
"""


def make_repo(tmp_path: Path, name: str, rc: int = 0) -> tuple[Path, Path, str]:
    """A checkout `name` tracking a bare origin, one commit ahead of it, behind a fake push gate."""
    repo = tmp_path / name
    fast_init(repo, "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    write(repo, "a.txt", "one\n")
    commit_all(repo, "one")
    remote = tmp_path / f"{name}.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "fetch", "-q", "origin")
    git(repo, "branch", "-q", "--set-upstream-to=origin/main")
    write(repo, "a.txt", "two\n")
    commit_all(repo, "two")
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(HOOK.format(rc=rc))
    hook.chmod(0o755)
    return repo, remote, git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
        HOOK_LOG=str(tmp_path / "hook.log"),
        GOH_LAND_TIMEOUT="60",
    )


def landq(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(LANDQ), *args],
        env=env,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
        text=True,
        timeout=120,
    )


def recorded(env: dict[str, str]) -> dict[str, dict]:
    """The outcome each item was given: `loopctl set ID landing JSON --json`, by item."""
    out = {}
    log = Path(env["FAKE_LOOPCTL_LOG"])
    for line in log.read_text().splitlines() if log.exists() else []:
        argv = json.loads(line)
        if argv[:1] == ["set"] and argv[2] == "landing":
            out[argv[1]] = json.loads(argv[3])
    return out


def runs(env: dict[str, str]) -> list[tuple[str, float, float]]:
    """(repo, start, end) per gate run, from the fake hook's log."""
    starts, out = {}, []
    for line in Path(env["HOOK_LOG"]).read_text().splitlines():
        what, name, at = line.split()
        if what == "start":
            starts[name] = float(at)
        else:
            out.append((name, starts.pop(name), float(at)))
    return out


def test_two_enqueued_pushes_run_serially_and_both_outcomes_land_in_the_queue(tmp_path, env):
    a, a_remote, a_sha = make_repo(tmp_path, "alpha")
    b, b_remote, b_sha = make_repo(tmp_path, "beta")
    for repo, sha, item in ((a, a_sha, "T.A"), (b, b_sha, "T.B")):
        done = landq(env, "--enqueue", str(repo), sha, "--item", item, "--no-start")
        assert done.returncode == 0, done.stdout + done.stderr
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    order = runs(env)
    assert [r[0] for r in order] == ["alpha", "beta"], order  # in the order enqueued
    assert order[0][2] <= order[1][1], f"the second gate started before the first ended: {order}"
    got = recorded(env)
    assert got["T.A"]["state"] == "landed" and got["T.A"]["sha"] == a_sha, got
    assert got["T.B"]["state"] == "landed" and got["T.B"]["sha"] == b_sha, got
    assert git(a_remote, "rev-parse", "main").strip() == a_sha
    assert git(b_remote, "rev-parse", "main").strip() == b_sha
    assert not list((tmp_path / "landq" / "pending").iterdir())


def test_a_red_gate_records_its_log_and_pushes_nothing(tmp_path, env):
    repo, remote, sha = make_repo(tmp_path, "gamma", rc=1)
    before = git(remote, "rev-parse", "main").strip()
    assert landq(env, "--enqueue", str(repo), sha, "--item", "T.C", "--no-start").returncode == 0
    landq(env, "--drain")
    got = recorded(env)["T.C"]
    assert got["state"] == "red", got
    assert got["sha"] == sha
    log = Path(got["log"])
    assert log.is_file() and "gate output for gamma" in log.read_text(), got
    assert git(remote, "rev-parse", "main").strip() == before != sha


def test_enqueue_refuses_a_sha_the_checkout_does_not_have(tmp_path, env):
    repo, _, _ = make_repo(tmp_path, "delta")
    done = landq(env, "--enqueue", str(repo), "0" * 40, "--item", "T.D", "--no-start")
    assert done.returncode != 0
    assert "0000000" in done.stdout + done.stderr
    assert not (tmp_path / "landq" / "pending").exists() or not list(
        (tmp_path / "landq" / "pending").iterdir()
    )


def test_a_request_a_dead_lander_was_running_is_run_again(tmp_path, env):
    """A reboot (or a SIGKILL) mid-push leaves the request in running/: the next lander runs it."""
    repo, remote, sha = make_repo(tmp_path, "eps")
    assert landq(env, "--enqueue", str(repo), sha, "--item", "T.E", "--no-start").returncode == 0
    q = tmp_path / "landq"
    (q / "running").mkdir(parents=True, exist_ok=True)
    for f in (q / "pending").iterdir():
        f.rename(q / "running" / f.name)  # what a lander killed mid-request leaves
    landq(env, "--drain")
    assert recorded(env)["T.E"]["state"] == "landed"
    assert git(remote, "rev-parse", "main").strip() == sha


@pytest.mark.load_sensitive
def test_enqueue_starts_a_lander_that_drains_without_anyone_watching(tmp_path, env):
    repo, remote, sha = make_repo(tmp_path, "zeta")
    done = landq(env, "--enqueue", str(repo), sha, "--item", "T.F")
    assert done.returncode == 0, done.stdout + done.stderr
    give_up = GiveUp(60)
    while "T.F" not in recorded(env) and not give_up.passed():
        time.sleep(0.2)
    assert recorded(env).get("T.F", {}).get("state") == "landed", recorded(env)
    assert git(remote, "rev-parse", "main").strip() == sha


def test_the_goh_binary_dispatches_land(goh, tmp_path, env):
    repo, _, sha = make_repo(tmp_path, "eta")
    argv = [str(goh), "land", "--enqueue", str(repo), sha, "--item", "T.G", "--no-start"]
    done = subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(list((tmp_path / "landq" / "pending").iterdir())) == 1
    assert os.environ.get("GOH_LANDQ_DIR") != env["GOH_LANDQ_DIR"]


# MODE LAND (GOH.LANDQLAND, owner 2026-11-15: "move the lanes onto the landing queue"): a lane
# hands its worktree's tip to the lander, which runs tools/land.sh bare there -- the one land lock,
# the land gate, the fast-forward of main -- and records the outcome; the lane waits on the record.
def _land_wt(repo: Path, tmp_path: Path) -> tuple[Path, str]:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    write(wt, "f.txt", "feature\n")
    git(wt, "add", "f.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "feature")
    return wt, git(wt, "rev-parse", "HEAD").strip()


def test_a_green_land_request_fast_forwards_main_and_records_landed(repo, tmp_path, env):
    wt, tip = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.L", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.L"]
    assert got["state"] == "landed" and got["sha"] == tip and got["mode"] == "land", got
    assert git(repo, "rev-parse", "HEAD").strip() == tip  # land.sh moved main, nothing else did
    assert "landed" in Path(got["log"]).read_text()


def test_a_red_land_gate_lands_nothing_and_records_the_log(repo, tmp_path, env):
    wt, tip = _land_wt(repo, tmp_path)
    before = git(repo, "rev-parse", "HEAD").strip()
    env = {**env, "GOH_LAND_GATE": "echo red-gate-says-why; false"}
    assert landq(env, "--enqueue", "--land", str(wt), "--item", "T.R", "--no-start").returncode == 0
    landq(env, "--drain")
    got = recorded(env)["T.R"]
    assert got["state"] == "red" and got["sha"] == tip, got
    assert "red-gate-says-why" in Path(got["log"]).read_text(), got
    assert git(repo, "rev-parse", "HEAD").strip() == before


def test_a_land_whose_tip_moved_after_enqueue_is_refused_unrun(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    before = git(repo, "rev-parse", "HEAD").strip()
    env = {**env, "GOH_LAND_GATE": "true"}
    assert landq(env, "--enqueue", "--land", str(wt), "--item", "T.M", "--no-start").returncode == 0
    write(wt, "f.txt", "moved\n")
    git(wt, "commit", "-qam", "moved", "--no-verify")
    landq(env, "--drain")
    got = recorded(env)["T.M"]
    assert got["state"] == "red" and "moved" in got["why"], got
    assert git(repo, "rev-parse", "HEAD").strip() == before  # not the enqueued tip, nor the new one


def test_enqueue_land_refuses_the_main_checkout_and_a_dirty_tree(repo, tmp_path, env):
    done = landq(env, "--enqueue", "--land", str(repo), "--item", "T.N", "--no-start")
    assert done.returncode == 2 and "main checkout" in done.stdout + done.stderr, done.stdout
    wt, _ = _land_wt(repo, tmp_path)
    write(wt, "f.txt", "uncommitted\n")
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.N", "--no-start")
    assert done.returncode == 2 and "uncommitted" in done.stdout + done.stderr, done.stdout
    pending = tmp_path / "landq" / "pending"
    assert not pending.exists() or not list(pending.iterdir())


@pytest.mark.load_sensitive
@pytest.mark.parametrize(("gate", "code", "state"), [("true", 0, "landed"), ("false", 1, "red")])
def test_wait_blocks_until_the_outcome_and_exits_by_it(repo, tmp_path, env, gate, code, state):
    """`goh land --wait ID` is the lane's one wait: no lander running (--no-start) is started."""
    wt, tip = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": gate}
    assert landq(env, "--enqueue", "--land", str(wt), "--item", "T.W", "--no-start").returncode == 0
    done = landq(env, "--wait", "T.W", "--timeout", "90")
    assert done.returncode == code, done.stdout + done.stderr
    assert state in done.stdout + done.stderr and tip[:8] in done.stdout + done.stderr
    assert recorded(env)["T.W"]["state"] == state


def test_wait_on_an_item_with_no_request_says_so(env):
    done = landq(env, "--wait", "T.NONE")  # refused at once: nothing to wait on
    assert done.returncode == 2 and "T.NONE" in done.stdout + done.stderr


def test_wait_on_an_item_never_takes_another_items_request(repo, tmp_path, env):
    """Request names are `<ns>-<item>`: an item `B` is not the request of an item `X-B`."""
    wt, _ = _land_wt(repo, tmp_path)
    assert landq(env, "--enqueue", "--land", str(wt), "--item", "X-B", "--no-start").returncode == 0
    done = landq(env, "--wait", "B")
    assert done.returncode == 2 and "no request for B" in done.stdout + done.stderr, done.stderr


def test_a_rebased_land_records_the_sha_on_main_as_landing_sha(repo, tmp_path, env):
    """GOH.LANDQSHA: loopctl done reads landing.sha, so it must be the commit main holds; the
    enqueued tip, which land.sh rebased away, keeps its own name."""
    wt, tip = _land_wt(repo, tmp_path)
    write(repo, "m.txt", "main moved\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "--no-verify", "-m", "main moved")
    env = {**env, "GOH_LAND_GATE": "true"}
    assert landq(env, "--enqueue", "--land", str(wt), "--item", "T.S", "--no-start").returncode == 0
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.S"]
    on_main = git(repo, "rev-parse", "HEAD").strip()
    assert got["state"] == "landed" and on_main != tip, got
    assert got["sha"] == on_main and got["enqueued"] == tip, got


# GOH.LANDERSTALECODE: the lander is long-lived, so a land that changes ITS code (tools/landq.py or
# lib/) must be honoured by the very next request, not by whenever the lander happens to restart.
EDIT_HOOK = """\
#!/bin/sh
python3 - "$CODE_COPY/tools/landq.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = 'outcome = {**outcome, "at": now()}'
assert old in s, "the marker's anchor moved"
open(p, "w").write(s.replace(old, 'outcome = {**outcome, "at": now(), "newcode": True}'))
PY
git -C "$CODE_COPY" commit -qam "land a change to the lander"
exit 0
"""


def test_a_land_that_changes_the_lander_is_honoured_by_the_next_request(tmp_path, env):
    code = tmp_path / "goh"
    # The lander and its closure; importlib's module names are said beside it.
    named = ("lib/bounded_run.py", "lib/gitrun.py", "lib/lock_order.py", "lib/lock_order.tsv")
    named += ("lib/land_prestep.py", "lib/killtree.py")
    named += ("checks/_gitutil.py",)
    sc.copy_closure(
        code,
        "tools/landq.py",
        extra=(
            *named,
            *sorted(
                str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "tui").glob("*") if p.is_file()
            ),
        ),
    )
    fast_init(code, "main")
    git(code, "config", "user.email", "t@example.invalid")
    git(code, "config", "user.name", "t")
    commit_all(code, "the lander's code")
    first, _, sha1 = make_repo(tmp_path, "mu")
    hook = first / ".git" / "hooks" / "pre-push"
    hook.write_text(EDIT_HOOK)  # this land changes the lander's code on its main
    second, _, sha2 = make_repo(tmp_path, "nu")
    env = {**env, "CODE_COPY": str(code)}
    lq = [sys.executable, str(code / "tools" / "landq.py")]

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [*lq, *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=patience(120),
            check=False,
            stdin=subprocess.DEVNULL,
        )

    for repo, sha, item in ((first, sha1, "T.M"), (second, sha2, "T.N")):
        got = run("--enqueue", str(repo), sha, "--item", item, "--no-start")
        assert got.returncode == 0, got.stdout + got.stderr
    done = run("--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    out = recorded(env)
    assert out["T.M"]["state"] == "landed" and "newcode" not in out["T.M"], out
    assert out["T.N"]["state"] == "landed", out
    assert out["T.N"].get("newcode") is True, "the next request ran the lander's OLD code"


# THE ENQUEUE PRE-STEP (GOH.ENQUEUEPRE): the land pre-step's set runs in the caller's worktree
# BEFORE a tip is queued, so a tree reader the lane never ran fails there, not inside the SERIAL
# queue (GOH.REUSEAUDIT's land held it 537 s, then went red on three of them). The lander's own
# pre-step stays the authority after a rebase.
def _pending(tmp_path: Path) -> list[Path]:
    pending = tmp_path / "landq" / "pending"
    return list(pending.iterdir()) if pending.exists() else []


def test_enqueue_refuses_a_tip_whose_pre_step_fails_and_names_the_test(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    pre = "echo 'FAILED tests/test_wiring.py::test_every_doc_row - planted'; exit 1"
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.P", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode != 0 and "tests/test_wiring.py::test_every_doc_row" in out, out
    assert "nothing enqueued" in out, out
    assert _pending(tmp_path) == []


def test_enqueue_queues_a_green_tip_after_running_the_pre_step_once(repo, tmp_path, env):
    wt, tip = _land_wt(repo, tmp_path)
    seen = tmp_path / "seen"
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": f"echo pre >> {seen}"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.G", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    assert seen.read_text() == "pre\n"  # the enqueue ran it, in the worktree, and landed nothing
    assert git(repo, "rev-parse", "HEAD").strip() != tip
    assert len(_pending(tmp_path)) == 1


def test_enqueue_queues_a_tip_whose_pre_step_hit_its_deadline_and_says_so(repo, tmp_path, env):
    """GOH.PRESTEPDEADLINE: no verdict is not red; the lander's gate decides."""
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "sleep 30", "GOH_LAND_PRE_MAX": "2s"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.D", "--no-start")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "no verdict" in out and "queued" in out, out
    assert len(_pending(tmp_path)) == 1


def test_enqueue_no_pre_names_its_reason_and_skips_the_pre_step(repo, tmp_path, env):
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": "exit 1"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.X", "--no-start", "--no-pre")
    assert done.returncode == 2 and "--no-pre needs a reason" in done.stdout + done.stderr
    assert _pending(tmp_path) == []
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.X", "--no-start",
                 "--no-pre", "lane ran the set by hand")  # fmt: skip
    assert done.returncode == 0 and "lane ran the set by hand" in done.stdout + done.stderr
    assert len(_pending(tmp_path)) == 1
