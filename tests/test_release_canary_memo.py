"""The release canary reuses a consumer's verdict while nothing that decides it moved
(GOH.CANARYMEMO, lib/canary_memo.py).

Every land whose gates differ from the last release swept the whole estate again: 205-232 s a
land, a tests-only land too. A side's verdict over a consumer is now kept under (the side's gate
content, its binary, the tools, the seed store, its grace, the consumer's committed HEAD), so a
second land with the same gates spends no run, a consumer whose HEAD moved is swept alone, and a
gate change sweeps every consumer again. The sides here are fake binaries that log each run, so
the count of runs is the measure, not the wall clock alone.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from _small_store import estate_state
from conftest import REPO_ROOT, hermetic_env

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
# Each test runs the canary in child processes, and one bounds its wall clock.
pytestmark = pytest.mark.load_sensitive
GREEN = "· a step (≤5s)\n✓ a step\n✓ all structural gates passed\n"


def _git(d: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=d,
                   capture_output=True, text=True, check=True,
                   stdin=subprocess.DEVNULL)  # fmt: skip


def _consumer(at: Path) -> Path:
    fast_init(at)
    write_gatesrc(at, "GOH_MAX_LINES=500\n")
    _git(at, "add", "-A")
    _git(at, "commit", "-qm", "first")
    return at


def _bin(at: Path, log: Path, side: str, exit_code: int = 0) -> Path:
    """A goh that logs `<side> <consumer HEAD> <GOH_GRACE>` and prints one green step."""
    body = (f'echo "{side} $(git rev-parse HEAD) ${{GOH_GRACE:-day}}" >> {log}\n'
            f"printf '{GREEN}'\nexit {exit_code}\n")  # fmt: skip
    at.write_text(f"#!/usr/bin/env bash\n{body}")
    at.chmod(0o755)
    return at


def _estate(tmp_path: Path) -> dict:
    goh = tmp_path / "goh"  # the gates tree both sides read: its content is the gate content
    (goh / "gates").mkdir(parents=True)
    (goh / "gates" / "a_gate.sh").write_text("echo gate\n")
    (goh / "tests").mkdir()
    a, b = _consumer(tmp_path / "c" / "a"), _consumer(tmp_path / "c" / "b")
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    (state.parent / "installed_repos").write_text(f"{a}\n{b}\n")
    (tmp_path / "estate").mkdir()
    log = tmp_path / "runs.log"
    log.write_text("")
    return {"goh": goh, "a": a, "b": b, "state": state, "log": log, "root": tmp_path,
            "base": _bin(tmp_path / "base-goh", log, "base"),
            "head": _bin(tmp_path / "head-goh", log, "head")}  # fmt: skip


def _canary(e: dict, *extra: str) -> tuple[subprocess.CompletedProcess, dict, list[str], float]:
    before = e["log"].read_text().splitlines()
    argv = [sys.executable, str(SCRIPT), "--root", str(e["root"] / "estate"), "--state",
            str(e["state"]), "--base-bin", str(e["base"]), "--head-bin", str(e["head"]),
            "--goh", str(e["goh"]), "--grace", "off", *extra]  # fmt: skip
    t0 = time.monotonic()
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
                       env=hermetic_env(GOH_SEED_DIR=str(e["root"] / "seeds")))  # fmt: skip
    secs = time.monotonic() - t0
    rec = estate_state(e["state"])["release_canary"]
    return r, rec, e["log"].read_text().splitlines()[len(before) :], secs


def test_a_second_land_with_the_same_gates_reuses_the_sweep_and_names_it(tmp_path):
    e = _estate(tmp_path)
    r1, rec1, ran1, _ = _canary(e)
    assert r1.returncode == 0 and rec1["verdict"] == "clear", r1.stdout + r1.stderr
    assert len(ran1) == 4, ran1  # two consumers, two sides
    r2, rec2, ran2, secs = _canary(e)
    out = r2.stdout + r2.stderr
    assert r2.returncode == 0 and rec2["verdict"] == "clear", out
    assert ran2 == [], ran2  # nothing run again
    assert "reused 4 side verdicts over 2 consumers" in out and rec1["ran_at"] in out, out
    assert rec2["memo"]["swept"] == {}, rec2["memo"]
    assert secs < 10, secs


def test_a_consumer_whose_head_moved_is_swept_alone(tmp_path):
    e = _estate(tmp_path)
    _canary(e)
    (e["a"] / "new.txt").write_text("x\n")
    _git(e["a"], "add", "-A")
    _git(e["a"], "commit", "-qm", "moved")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=e["a"], capture_output=True, text=True,
                         check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    r, rec, ran, _ = _canary(e)
    assert r.returncode == 0, r.stdout + r.stderr
    assert sorted(ran) == [f"base {sha} day", f"head {sha} day"], ran
    assert list(rec["memo"]["swept"]) == [str(e["a"])], rec["memo"]
    assert list(rec["memo"]["reused"]) == [str(e["b"])], rec["memo"]


def test_a_gate_content_change_sweeps_every_consumer_again(tmp_path):
    e = _estate(tmp_path)
    _canary(e)
    (e["goh"] / "tests" / "only_a_test.py").write_text("x = 1\n")  # not a gate input: reused
    _, _, ran, _ = _canary(e)
    assert ran == [], ran
    (e["goh"] / "gates" / "a_gate.sh").write_text("echo gate, changed\n")  # the planted change
    r, _, ran, _ = _canary(e)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(ran) == 4, ran


def test_a_head_binary_change_reruns_only_the_head_side(tmp_path):
    e = _estate(tmp_path)
    _canary(e)
    e["head"].write_text(e["head"].read_text() + "# rebuilt\n")
    _, _, ran, _ = _canary(e)
    assert sorted(x.split()[0] for x in ran) == ["head", "head"], ran  # the base side holds


def test_the_grace_pass_is_keyed_apart_and_a_verdict_that_did_not_judge_is_not_kept(tmp_path):
    e = _estate(tmp_path)
    _canary(e)
    _, _, ran, _ = _canary(e, "--grace", "on")
    assert sorted(ran) == sorted(x for x in ran if x.endswith(" over")) and len(ran) == 2, ran
    _bin(e["head"], e["log"], "head", exit_code=3)  # an error: no verdict to keep
    _canary(e)
    _, _, ran, _ = _canary(e)
    assert sorted(x.split()[0] for x in ran) == ["head", "head"], ran


def test_fresh_reuses_nothing(tmp_path):
    e = _estate(tmp_path)
    _canary(e)
    _, rec, ran, _ = _canary(e, "--fresh")
    assert len(ran) == 4 and not rec["memo"]["reused"], ran
