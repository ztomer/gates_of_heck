"""tools/ship.py (`goh ship`): the detached, time-bounded local landing pipeline (GOH.SHIP).

An agent's work ends at "committed, local gate green, goh ship started"; what follows is
push (through the pre-push gate) -> the repo's release -> install -> the `loopctl verify` card ->
`loopctl done`, run by nobody's turn. So the three things that must hold without anyone watching:
the success path records the item done, a failing step names itself and carries its log tail (the
coordinator resumes an agent with only that), and the time bound kills a hung step's whole process
group, leaving no orphan. The loopctl here is a fake that records its argv; the real one is
exercised end to end by the coordinator's repo (.loop tests/test_ship_done.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env, run_goh
from timing_bounds import GiveUp, assert_sooner

SHIP = REPO_ROOT / "tools" / "ship.py"
# The fake loopctl: every call appended to $FAKE_LOOPCTL_LOG; `verify` exits $FAKE_VERIFY_RC.
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "verify":
    print("fake card line 1")
    sys.exit(int(os.environ.get("FAKE_VERIFY_RC", "0")))
"""


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a bare `origin` it tracks, tagged v1.0.0 and pushed, then one commit on top."""
    remote = tmp_path / "remote.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "tag", "v1.0.0")
    git(repo, "push", "-q", "-u", "origin", "main", "v1.0.0")
    (repo / "f.txt").write_text("change\n")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: change")
    (tmp_path / "loopctl.py").write_text(FAKE_LOOPCTL)
    return repo


def _env(tmp_path: Path, **over: str) -> dict[str, str]:
    base = {
        "GOH_LOOPCTL": str(tmp_path / "loopctl.py"),
        "FAKE_LOOPCTL_LOG": str(tmp_path / "loopctl.calls"),
        "LOOPCTL_ROOT": str(tmp_path),
        "GOH_SHIP_RELEASE": 'git tag "v$GOH_SHIP_VERSION" && git push -q origin "v$GOH_SHIP_VERSION"',
    }
    return hermetic_env(drop_git=True, **{**base, **over})


def _ship(repo: Path, tmp_path: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SHIP), "--repo", str(repo), "--out",
                           str(tmp_path / "ship.json"), *args],
                          env=_env(tmp_path, **env), capture_output=True, text=True, timeout=120,
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _result(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "ship.json").read_text())


def _calls(tmp_path: Path) -> list[list[str]]:
    log = tmp_path / "loopctl.calls"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def test_the_success_path_ships_every_step_and_records_the_item_done(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", "--summary", "did the thing")
    assert r.returncode == 0, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    assert [(s["name"], s["status"]) for s in res["steps"]] == [
        ("push", "ok"),
        ("release", "ok"),
        ("install", "skipped"),
        ("verify", "ok"),
        ("done", "ok"),
    ]
    assert res["version"] == "1.1.0"  # feat: since v1.0.0 -> minor
    assert git(shipped, "ls-remote", "origin", "refs/tags/v1.1.0").strip()
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] in _calls(tmp_path)
    # nothing but a mark per step is printed
    marks = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert all(("✓" in ln) or ("·" in ln) for ln in marks), r.stdout
    assert len(marks) == 5


@pytest.mark.parametrize("step", ["push", "release", "install", "verify"])
def test_a_failing_step_names_itself_and_carries_its_last_40_log_lines(shipped, tmp_path, step):
    noisy = 'for i in $(seq 1 100); do echo "line $i"; done; exit 3'
    env: dict[str, str] = {}
    if step == "push":
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text("#!/bin/sh\n" + noisy + "\n")
        (hooks / "pre-push").chmod(0o755)
        git(shipped, "config", "core.hooksPath", str(hooks))
    elif step == "release":
        env["GOH_SHIP_RELEASE"] = noisy
    elif step == "install":
        env["GOH_SHIP_INSTALL"] = noisy
    else:
        env["FAKE_VERIFY_RC"] = "1"
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", **env)
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["status"] == "failed"
    assert res["failed_step"] == step
    if step == "verify":
        assert "fake card line 1" in res["tail"]
    elif step == "push":  # git's own refusal follows the hook's output
        assert "line 100" in res["tail"] and len(res["tail"]) == 40
    else:
        assert res["tail"][-1] == "line 100" and res["tail"][0] == "line 61"
        assert len(res["tail"]) == 40
    assert ["done", "X.ONE", "--shipped", str(tmp_path / "ship.json")] not in _calls(tmp_path)
    assert f"✗ {step}" in r.stdout + r.stderr


def _sleepers(marker: str) -> list[str]:
    ps = subprocess.run(["ps", "-Ao", "pid=,ppid=,command="], capture_output=True, text=True,
                        check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    return [ln for ln in ps.splitlines() if marker in ln and "ps -Ao" not in ln]


def test_the_bound_kills_a_hung_step_and_its_whole_group_leaving_no_orphan(shipped, tmp_path):
    marker = f"sleep 731.{os.getpid() % 1000}"
    hung = f"{marker} & {marker} & ( {marker} & wait ) & wait"
    t0 = time.monotonic()
    r = _ship(shipped, tmp_path, "--wait", GOH_SHIP_RELEASE=hung, GOH_SHIP_TIMEOUT="2")
    # the pass: the 2 s bound, then up to the 5 s grace; the failure: the sleeps run out (731 s)
    assert_sooner(time.monotonic() - t0, 8, 731, "a hung step under a 2 s bound")
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "release"
    assert res["steps"][-1]["status"] == "timed_out"
    left = _sleepers(marker)
    assert not left, f"orphans (PPID=1 once their shell died): {left}"


def test_started_detached_it_returns_at_once_and_the_worker_finishes_alone(shipped, tmp_path):
    t0 = time.monotonic()
    r = _ship(shipped, tmp_path, "--item", "X.ONE", GOH_SHIP_INSTALL="sleep 8")
    assert r.returncode == 0, r.stdout + r.stderr
    # the pass: plan and spawn; the failure: a start that ran the pipeline, its 8 s install included
    assert_sooner(time.monotonic() - t0, 1.5, 8, "goh ship's detached start")
    assert "goh ship started" in r.stdout
    assert str(tmp_path / "ship.json") in r.stdout
    give_up = GiveUp(90)
    while not give_up.passed() and _result(tmp_path)["status"] == "running":
        time.sleep(0.5)
    res = _result(tmp_path)
    assert res["status"] == "ok", res
    status = subprocess.run([sys.executable, str(SHIP), "--status", str(tmp_path / "ship.json")],
                            capture_output=True, text=True, check=False,
                            stdin=subprocess.DEVNULL, env=_env(tmp_path))  # fmt: skip
    assert status.returncode == 0, status.stdout + status.stderr


def test_a_repo_that_tags_but_declares_no_release_is_refused_before_it_starts(shipped, tmp_path):
    r = _ship(shipped, tmp_path, GOH_SHIP_RELEASE="")
    assert r.returncode == 2
    assert "GOH_SHIP_RELEASE" in r.stderr
    assert not (tmp_path / "ship.json").exists()


def test_a_dirty_tree_is_refused_before_it_starts(shipped, tmp_path):
    (shipped / "README.md").write_text("dirty\n")
    r = _ship(shipped, tmp_path)
    assert r.returncode == 2
    assert "uncommitted" in r.stderr


def test_a_head_already_tagged_skips_the_release(shipped, tmp_path):
    git(shipped, "tag", "v1.0.1")
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 0, r.stdout + r.stderr
    rel = next(s for s in _result(tmp_path)["steps"] if s["name"] == "release")
    assert rel["status"] == "skipped" and "v1.0.1" in rel["why"]


@pytest.mark.parametrize(
    ("subject", "want"),
    [("fix: a", "1.0.1"), ("feat: a", "1.1.0"), ("feat!: a", "2.0.0"), ("chore: a", "1.0.1")],
)
def test_the_next_version_follows_conventional_commits(shipped, tmp_path, subject, want):
    git(shipped, "reset", "-q", "--hard", "v1.0.0")
    (shipped / "g.txt").write_text(subject)
    git(shipped, "add", "g.txt")
    git(shipped, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", subject)
    r = _ship(shipped, tmp_path, "--wait")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["version"] == want


def test_goh_ship_runs_this_file_and_passes_its_exit_code(shipped, tmp_path):
    r = run_goh(shipped, "ship", "--out", str(tmp_path / "ship.json"), "--wait",
                env=_env(tmp_path))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert _result(tmp_path)["status"] == "ok"
    r = run_goh(shipped, "ship", "--status", str(tmp_path / "nope.json"), env=_env(tmp_path))
    assert r.returncode == 2 and "nope.json" in r.stderr
