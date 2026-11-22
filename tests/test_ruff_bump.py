"""A ruff on the box newer than goh's pin starts its own bump-only land (GOH.RUFFBUMPLAND).

GOH.RUFFCURRENCY made every goh land carry the bump, but nothing started a land when no goh work
was queued: every consumer gate (gates/_py.sh goh_py_ruff_pinned) stayed red until somebody's next
goh land. Here the next release is faked as in tests/test_ruff_currency.py (a `ruff` on PATH and an
interpreter whose `-m ruff` answer one patch past the pin), the queue is empty, and the lander's
idle pass must land the bump alone, through the normal queue and its gate, exactly once.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_ruff_currency import GATE, NEXT, PIN, _box, _goh_tree
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import idle_suite
import ruff_bump
import ruff_currency

LANDQ = REPO_ROOT / "tools" / "landq.py"
ITEM = ruff_currency.item(NEXT)


@pytest.fixture
def box(repo: Path, tmp_path: Path, monkeypatch) -> dict[str, str]:
    """goh's tree on main, the box one ruff release past its pin, an empty queue, outside any
    goh suite's markers (in a suite the bump is off, so no test starts a real land)."""
    _goh_tree(repo)
    env = _box(tmp_path, NEXT, PIN)
    for k in (*idle_suite.IN_SUITE, idle_suite.OFF_ENV, ruff_bump.OFF_ENV):
        env.pop(k, None)
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "loopctl.py").write_text("import sys\n")
    env.update(GOH_LANDQ_DIR=str(tmp_path / "landq"), GOH_LIVE_ROOT=str(repo),
               GOH_LOOPCTL=str(tmp_path / "loopctl.py"), GOH_SHIP_DIR=str(tmp_path / "ships"),
               GOH_LAND_GATE=GATE, GOH_LAND_TIMEOUT="120")  # fmt: skip
    env["GOH_LOOP_QUEUE"] = str(tmp_path / "queue.json")  # never the real queue
    for k in ("GOH_PY", "PATH", "GOH_SHIP_DIR", "GOH_LOOPCTL", "GOH_LOOP_QUEUE"):
        monkeypatch.setenv(k, env[k])
    return env


def _landq(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(LANDQ), *args], env=env, capture_output=True,
                          text=True, timeout=patience(300), stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip


@pytest.mark.load_sensitive  # a land through the real queue, waited for under patience()
def test_an_idle_lander_lands_the_bump_alone_with_nothing_queued(repo, tmp_path, box) -> None:
    before = git(repo, "rev-parse", "HEAD").strip()
    r = _landq(box, "--drain")  # nothing pending: only its idle pass can start a land
    assert r.returncode == 0, r.stdout + r.stderr
    w = _landq(box, "--wait", ITEM, "--timeout", str(patience(240)))
    assert w.returncode == 0, r.stdout + r.stderr + w.stdout + w.stderr
    assert git(repo, "log", "--format=%s", f"{before}..HEAD").splitlines() == [
        f"{ITEM}: ruff {NEXT} is the house pin"
    ]
    bump = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(bump) == [f"changelog/{ITEM}.md", "gates/required_tools.tsv"]
    assert ruff_currency.pin_of((repo / "gates" / "required_tools.tsv").read_text()) == NEXT
    ledger = json.loads((tmp_path / "landq" / "ruff-bump" / f"{NEXT}.json").read_text())
    assert ledger["state"] == "enqueued" and ledger["item"] == ITEM
    again = _landq(box, "--ruff-bump")  # the on-demand look: main pins what the box runs now
    assert again.returncode == 0 and "ruff bump: nothing" in again.stdout + again.stderr


def _fake_enqueue(seen: list):
    def enqueue(wt: Path, item: str) -> tuple[int, str]:
        seen.append((git(wt, "log", "--format=%s", "-1").strip(), item))
        return 0, "enqueued"

    return enqueue


def test_one_release_is_bumped_once_even_after_a_failure(repo, tmp_path, box) -> None:
    q, seen = tmp_path / "landq", []
    fail = lambda wt, item: (1, "refused")  # noqa: E731 -- a one-line stand-in
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=fail) == "failed"
    assert json.loads((q / "ruff-bump" / f"{NEXT}.json").read_text())["state"] == "failed"
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=_fake_enqueue(seen)) == "done-before"
    assert seen == []


def test_a_land_or_a_ship_in_flight_leaves_it_to_the_next_look(repo, tmp_path, box) -> None:
    q, seen = tmp_path / "landq", []
    (q / "pending").mkdir(parents=True)
    (q / "pending" / "1-X.json").write_text("{}")
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=_fake_enqueue(seen)) == "busy"
    (q / "pending" / "1-X.json").unlink()
    ships = tmp_path / "ships" / repo.name
    ships.mkdir(parents=True)
    rec = {"status": "running", "pid": os.getpid()}
    (ships / "20261120T000000Z-abcdef12.json").write_text(json.dumps(rec))
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=_fake_enqueue(seen)) == "busy"
    (ships / "20261120T000000Z-abcdef12.json").write_text(json.dumps({**rec, "status": "ok"}))
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=_fake_enqueue(seen)) == "enqueued"
    assert seen == [(f"{ITEM}: ruff {NEXT} is the house pin", ITEM)]
    assert not (q / "ruff-bump" / f"{NEXT}.json").read_text().count("failed")


def test_a_box_at_the_pin_starts_nothing(repo, tmp_path, box, monkeypatch) -> None:
    monkeypatch.setenv("GOH_PY", _box(tmp_path / "at", PIN, PIN)["GOH_PY"])
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    q = tmp_path / "landq"
    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=_fake_enqueue([])) == "nothing"
    assert not (q / "ruff-bump").exists()


def test_a_consumer_gate_meeting_a_newer_ruff_starts_the_bump(tmp_path) -> None:
    """The red itself starts the fix: goh_py_ruff_pinned asks goh for the bump before it dies."""
    log = tmp_path / "py.log"
    py = tmp_path / "py"
    py.write_text(f'#!/bin/sh\necho "$*" >>"{log}"\ncase "$*" in *--pin) echo {PIN};; esac\n')
    py.chmod(0o755)
    script = (f'. "{REPO_ROOT}/gates/_py.sh"; die() {{ echo "$*" >&2; exit 1; }}; '
              f'GOH_PY="{py}"; GOH_NAME=t; RUN=python3; '
              f'goh_py_ruff_pinned "ruff {NEXT}"')  # fmt: skip
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                       stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip
    assert r.returncode == 1 and f"goh pins ruff {PIN}" in r.stderr, r.stdout + r.stderr
    house = f"-S {REPO_ROOT}/gates/ruff_house.py"
    assert log.read_text().splitlines() == [f"{house} --pin", f"{house} --bump-if-newer {NEXT}"]


def test_the_gates_ask_starts_goh_land_ruff_bump_detached(monkeypatch) -> None:
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    import ruff_house

    seen: list[list[str]] = []
    monkeypatch.setattr(os, "access", lambda *_: True)
    monkeypatch.setattr(subprocess, "run", lambda argv, **_: seen.append(argv))
    ruff_house.bump_if_newer(ruff_house.RUFF)  # the pin itself: nothing to ask
    ruff_house.bump_if_newer(NEXT)
    assert seen == [[str(REPO_ROOT / "bin" / "goh"), "land", "--ruff-bump", "--detach"]]


def test_the_bump_files_its_queue_item_before_its_land_is_enqueued(repo, tmp_path, box) -> None:
    """GOH.RUFFBUMPITEM: a land goh starts by itself has a queue item, filed through the one
    funnel (lib/reuse_fault._file, a regression) BEFORE the enqueue, so the loop ships it."""
    log = tmp_path / "filed.jsonl"
    (tmp_path / "loopctl.py").write_text(
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n")  # fmt: skip
    q, order = tmp_path / "landq", []

    def enqueue(wt: Path, item: str) -> tuple[int, str]:
        order.append(log.read_text() if log.exists() else "")
        return 0, "enqueued"

    assert ruff_bump.run(q, repo, say=lambda _: None, enqueue=enqueue) == "enqueued"
    argv = json.loads(order[0].splitlines()[0])
    filed = json.loads(argv[argv.index("--json") + 1])
    assert argv[argv.index("--json") - 1] == "file"
    assert filed["id"] == ITEM and filed["regression"] is True and filed["repo"] == "gates_of_heck"
