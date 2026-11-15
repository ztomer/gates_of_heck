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
from timing_bounds import GiveUp

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
