"""A land red on its last attempt, or enqueued `--no-pre`, gates alone (GOH.REDALONE).

lib/landq_alone.py. Each item here is enqueued as a lane enqueues it (tests/test_landq_batch.py's
queue and gate, which logs the subject of each tree it judges), so the mark is the one the real
enqueue puts on the request: GOH.NOPREBATCH's rule read a `no_pre` key no enqueue wrote, and its
tests planted the key by hand.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq_batch import (
    FAKE_LOOPCTL,
    GATE,
    enqueue_all,
    gate_runs,
    in_process,
    landq,
    lane,
    recorded,
)

sys.path.insert(0, str(REPO_ROOT / "lib"))
import land_records
import landq_alone

_SEQ = itertools.count(1)  # the records' names, in the order planted: as the lander's ns stamps


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq_batch.py's queue and gate."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="120", GOH_LAND_GATE=GATE,
                        GATE_LOG=str(tmp_path / "gate.log"))  # fmt: skip


def _enqueue(env: dict[str, str], wt: Path, item: str, *no_pre: str) -> None:
    if not no_pre:
        enqueue_all(env, [(wt, item)])
        return
    done = in_process(env, "--enqueue", "--foreground", "--land", str(wt), "--item", item,
                      "--no-start", *no_pre)  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr


def _record(env: dict[str, str], item: str, state: str, why: str) -> None:
    """A past attempt of ITEM in the queue's records, as the lander files one."""
    lq = Path(env["GOH_LANDQ_DIR"])
    land_records.write(lq, f"{next(_SEQ):020d}-{item}", {"state": state, "why": why})


def _two(repo: Path, tmp_path: Path, env: dict[str, str], *second: str) -> list[str]:
    """Two lands drained: one gate run if they stacked, two if one of them gates alone."""
    wts = [(lane(repo, tmp_path, n, {f"{n}.txt": f"{n}\n"}), f"T.{n.upper()}")
           for n in ("one", "two")]  # fmt: skip
    for i, (wt, item) in enumerate(wts):
        _enqueue(env, wt, item, *(second if i == 1 else ()))
    assert landq(env, "--drain").returncode == 0
    return [item for _, item in wts]


def test_a_no_pre_land_joins_no_batch_before_it(repo, tmp_path, env):
    items = _two(repo, tmp_path, env, "--no-pre", "the set ran by hand")
    assert gate_runs(env) == ["one", "two"], gate_runs(env)
    got = recorded(env)
    assert all(got[i]["state"] == "landed" for i in items), got


def test_a_land_red_on_its_last_attempt_gates_alone_until_it_lands(repo, tmp_path, env):
    """The marked land is the FIRST here: no later land joins it either."""
    _record(env, "T.ONE", "red", "exit 1")
    items = _two(repo, tmp_path, env)
    assert gate_runs(env) == ["one", "two"], gate_runs(env)
    got = recorded(env)
    assert all(got[i]["state"] == "landed" for i in items), got
    assert landq_alone.red_last(Path(env["GOH_LANDQ_DIR"]), "T.ONE") == ""  # it landed once


def test_a_red_that_judged_no_code_and_a_landed_last_attempt_batch(repo, tmp_path, env):
    _record(env, "T.ONE", "red", "conflict: did not stack on 0123abcd")
    _record(env, "T.TWO", "red", "exit 1")
    _record(env, "T.TWO", "landed", "")
    _two(repo, tmp_path, env)
    assert gate_runs(env) == ["two"], gate_runs(env)  # one stack, one gate


def test_red_last_names_the_newest_red_of_the_items_own_code(tmp_path):
    q = tmp_path / "landq"
    assert landq_alone.red_last(q, "T.X") == ""  # no records at all
    _record({"GOH_LANDQ_DIR": str(q)}, "T.X", "red", "enqueue pre-step: tests failed\nlog")
    _record({"GOH_LANDQ_DIR": str(q)}, "T.XY", "landed", "")  # another item's, never this one's
    assert landq_alone.red_last(q, "T.X") == "enqueue pre-step: tests failed"
    _record({"GOH_LANDQ_DIR": str(q)}, "T.X", "red", "tip moved after enqueue")
    assert landq_alone.red_last(q, "T.X") == ""
