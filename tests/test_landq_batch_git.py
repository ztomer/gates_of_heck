"""tools/landq.py's batch when git fails (GOH.BATCHGITFAIL).

The batch's git() answered '' on any failure, and settle() keyed `red_trees` by the tree it read:
after ONE failed rev-parse every later failed rev-parse looked like a tree already judged red, so a
stack was reported `gated red` without being gated; on a landed stack an empty main recorded
sha='' on each done record. A git failure now stops the batch step loudly: the requests it had not
judged stay pending and are tried again (and recorded red, with the git error, after RETRIES
passes); one the gate had already landed is never recorded with an empty sha. The failures here are
injected at `gitrun.run_reader`, the one door the lander's git reads go through.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git, hermetic_env
from test_landq_batch import FAKE_LOOPCTL, GATE, enqueue_all, gate_runs, lane

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gitrun
import landq_batch

LANDQ = REPO_ROOT / "tools" / "landq.py"
TREE = "HEAD^{tree}"


@pytest.fixture
def lq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The lander module, in this process, under the batch tests' hermetic environment."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    env = hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
        GOH_LAND_TIMEOUT="120",
        GOH_LAND_GATE=GATE,
        GATE_LOG=str(tmp_path / "gate.log"),
    )
    for key in [k for k in os.environ if k.startswith(("GIT_", "GOH_")) and k not in env]:
        monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(landq_batch, "READ_PAUSE", 0.0)
    monkeypatch.setattr(landq_batch, "PAUSE", 0.0)
    spec = importlib.util.spec_from_file_location("landq_git_probe", LANDQ)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fail_when(monkeypatch: pytest.MonkeyPatch, when) -> list[tuple[str, ...]]:
    """gitrun.run_reader fails (exit 128) for each call WHEN(args, kwargs) names; the calls seen."""
    real, seen = gitrun.run_reader, []

    def flaky(args, **kw):
        if when(tuple(args), kw):
            seen.append(tuple(args))
            return subprocess.CompletedProcess(args, 128, "", "fatal: injected git failure")
        return real(args, **kw)

    monkeypatch.setattr(gitrun, "run_reader", flaky)
    return seen


def enqueue_three(repo: Path, tmp_path: Path, env: dict[str, str], middle: dict[str, str]):
    wts = [
        (lane(repo, tmp_path, "one", {"one.txt": "1\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", middle), "T.TWO"),
        (lane(repo, tmp_path, "three", {"three.txt": "3\n"}), "T.THREE"),
    ]
    enqueue_all(env, wts)
    q = Path(env["GOH_LANDQ_DIR"])
    paths = []
    for p in sorted((q / "pending").glob("*.json")):
        (q / "running").mkdir(exist_ok=True)
        paths.append(p.replace(q / "running" / p.name))
    return q, wts, paths


def run_batch(lq, q: Path, paths: list[Path], say: list[str]) -> list[dict]:
    kit = dataclasses.replace(lq.batch_kit(), say=say.append)
    return landq_batch.run(q, paths, kit, lq.run_one)


def test_a_failed_tree_read_leaves_the_stack_pending_and_never_gates_a_guess(
    repo, tmp_path, lq, monkeypatch
):
    env = dict(os.environ)
    q, wts, paths = enqueue_three(repo, tmp_path, env, {"two.txt": "2\n"})
    seen = fail_when(monkeypatch, lambda a, kw: TREE in a)
    say: list[str] = []
    outs = run_batch(lq, q, paths, say)
    assert [o["state"] for o in outs] == ["pending"] * 3, outs
    assert seen and all("injected git failure" in o["why"] for o in outs), outs
    assert any("injected git failure" in line for line in say), say  # loud in the lander log
    assert "injected git failure" in Path(outs[0]["log"]).read_text()  # and in the item's own log
    assert gate_runs(env) == [], gate_runs(env)  # no gate judged a tree nobody could name
    enqueued = [json.loads(p.read_text())["sha"] for p in paths]
    got = [git(wt, "rev-parse", "HEAD").strip() for wt, _ in wts]
    assert got == enqueued, got  # each lane's branch is back at its enqueued commit


def test_a_tree_read_failing_after_a_red_gate_is_no_red_verdict_for_an_ungated_tree(
    repo, tmp_path, lq, monkeypatch
):
    env = dict(os.environ)
    q, _, paths = enqueue_three(repo, tmp_path, env, {"two.txt": "2\n", "bad.txt": "planted\n"})
    calls: list[int] = []

    def later(args: tuple[str, ...], kw: dict) -> bool:
        if TREE not in args:
            return False
        calls.append(1)
        return len(calls) > 1  # the stack's own tree reads fine; its halves' do not

    fail_when(monkeypatch, later)
    outs = run_batch(lq, q, paths, [])
    assert gate_runs(env) == ["three"], gate_runs(env)  # the stack, red; nothing else was gated
    assert [o["state"] for o in outs] == ["pending"] * 3, outs  # never `red` for an ungated half
    assert not any("gated red" in Path(o["log"]).read_text() for o in outs)


def test_a_stack_landed_with_an_unreadable_main_is_never_recorded_with_an_empty_sha(
    repo, tmp_path, lq, monkeypatch
):
    env = dict(os.environ)
    q, _, paths = enqueue_three(repo, tmp_path, env, {"two.txt": "2\n"})
    gate_log = Path(env["GATE_LOG"])
    seen = fail_when(
        monkeypatch,
        lambda a, kw: a == ("rev-parse", "HEAD") and kw.get("repo") == repo and gate_log.exists(),
    )
    outs = run_batch(lq, q, paths, [])
    assert len(seen) == landq_batch.READ_TRIES, seen  # main's HEAD was read again before giving up
    assert all(o["state"] == "red" and "main's HEAD is unreadable" in o["why"] for o in outs), outs
    assert all(o["sha"] and o["sha"] == json.loads(p.read_text())["sha"]
               for o, p in zip(outs, paths, strict=True)), outs  # fmt: skip
    assert "injected git failure" in outs[0]["why"]


def test_a_single_land_with_an_unreadable_main_is_never_recorded_with_an_empty_sha(
    repo, tmp_path, lq, monkeypatch
):
    env = dict(os.environ)
    q, _, paths = enqueue_three(repo, tmp_path, env, {"two.txt": "2\n"})
    only = paths[0]
    for other in paths[1:]:
        other.unlink()
    gate_log = Path(env["GATE_LOG"])
    fail_when(
        monkeypatch,
        lambda a, kw: a == ("rev-parse", "HEAD") and kw.get("repo") == repo and gate_log.exists(),
    )
    out = lq.run_one(q, only)
    assert out["state"] == "red" and out["sha"], out
    assert "main's HEAD is unreadable" in out["why"] and "injected git failure" in out["why"], out


def test_a_request_a_git_failure_left_pending_is_tried_again_then_recorded_red(tmp_path):
    q = tmp_path / "landq"
    for d in ("pending", "running"):
        (q / d).mkdir(parents=True)
    path = q / "running" / "r1.json"
    path.write_text("{}")
    pending = {"state": "pending", "why": "git rev-parse: boom", "sha": "abc", "log": ""}
    fails: dict[str, int] = {}
    say: list[str] = []
    for _ in range(landq_batch.RETRIES - 1):
        assert landq_batch.hold(q, path, pending, fails, say.append) is None
        assert (q / "pending" / "r1.json").is_file() and not path.exists()
        (q / "pending" / "r1.json").replace(path)
    final = landq_batch.hold(q, path, pending, fails, say.append)
    assert final and final["state"] == "red" and "boom" in final["why"], final
    assert fails == {} and path.exists()  # recorded: the lander unlinks it, not hold
    assert any("boom" in line for line in say), say
    done = {"state": "landed", "sha": "abc", "log": ""}
    assert landq_batch.hold(q, path, done, {"r1": 2}, say.append) == done  # a verdict resets it


def test_the_lander_retries_a_pending_request_on_its_next_pass(repo, tmp_path, lq, monkeypatch):
    monkeypatch.chdir(tmp_path)  # drain() makes the queue dir this process's own (GOH.LANDERCWD)
    env = dict(os.environ)
    q = Path(env["GOH_LANDQ_DIR"])
    wts = [(lane(repo, tmp_path, "one", {"one.txt": "1\n"}), "T.ONE")]
    enqueue_all(env, wts)
    calls: list[int] = []
    real = landq_batch.run

    def once_failing(qd, paths, kit, run_one):
        calls.append(1)
        if len(calls) == 1:
            return [{"state": "pending", "why": "git: boom", "sha": "s", "log": "", "repo": "r"}]
        return real(qd, paths, kit, run_one)

    monkeypatch.setattr(landq_batch, "run", once_failing)
    assert lq.drain() == 0
    assert len(calls) == 2, calls  # tried again in the same drain, not stranded
    assert not list((q / "pending").glob("*.json")) and not list((q / "running").glob("*.json"))
    assert gate_runs(env) == ["one"]
