"""The batch's ratchet phase (GOH.STACKRATCHETFIRST, lib/landq_ratchet.py).

Batch 01795449296573240000 (ROADMAPIDS, NOPREBATCH, FSEVCHURN) gated 40 min, went red on
tests/test_self_walk.py -- a tree-wide judge red only on the STACK -- then spent two more gates
bisecting. The judges that depend on the stack, not on one item, are minutes on the stacked tree:
run them first, evict the item whose own diff turns one red, and gate the rest once.

The ratchet here is a count, the shape of a seeded ratchet: GOH_LAND_CHECK_STEPS is red when the
tree holds more than one `r-*.txt`, so each item is green alone (its enqueue check passes) and two
that add one each are red together; the gate is red on the same count, as the full gate is.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import commit_all, hermetic_env, write
from test_landq_batch import FAKE_LOOPCTL, enqueue_all, gate_runs, landq, lane, recorded

COUNT = '[ "$(ls r-*.txt 2>/dev/null | wc -l)" -le 1 ]'
GATE = (
    'git log -1 --format=%s >>"$GATE_LOG"; '
    'if ! [ "$(ls r-*.txt 2>/dev/null | wc -l)" -le 1 ]; then echo planted-ratchet-red; exit 1; fi'
)


@pytest.fixture
def ratchet_env(tmp_path: Path) -> dict[str, str]:
    """tests/test_landq_batch.py's queue, its gate and land check the count above."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"),
                        GOH_LOOPCTL=str(fake), FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
                        GOH_LAND_TIMEOUT="120", GOH_LAND_GATE=GATE, GOH_LAND_CHECK_STEPS=COUNT,
                        GATE_LOG=str(tmp_path / "gate.log"))  # fmt: skip


def test_a_middle_item_that_breaks_a_ratchet_is_evicted_and_the_rest_gate_once(
    repo, tmp_path, ratchet_env
):
    env = ratchet_env
    wts = [
        (lane(repo, tmp_path, "one", {"r-1.txt": "1\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"r-2.txt": "2\n"}), "T.TWO"),
        (lane(repo, tmp_path, "three", {"three.txt": "3\n"}), "T.THREE"),
    ]
    enqueue_all(env, wts)
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.THREE"]["state"] == "landed", got
    assert got["T.TWO"]["state"] == "red" and "ratchet" in got["T.TWO"]["why"], got
    assert gate_runs(env) == ["three"], gate_runs(env)  # the rest, gated ONCE; no bisect
    own = Path(got["T.TWO"]["log"])  # its own log, naming the judge its diff turned red
    assert own.name.endswith("T.TWO.log") and COUNT in own.read_text(), own.read_text()
    assert not (repo / "r-2.txt").exists() and (repo / "three.txt").exists()


def test_a_head_item_red_on_the_stack_is_evicted_when_main_is_green(repo, tmp_path, ratchet_env):
    """The lowest item: its tree is main plus its diff, so main is judged too, and only a green
    main makes the item the one to evict."""
    env = ratchet_env
    wts = [(lane(repo, tmp_path, "one", {"r-1.txt": "1\n"}), "T.ONE")]
    enqueue_all(env, wts)
    write(repo, "r-0.txt", "0\n")  # another land moved main after T.ONE's enqueue
    commit_all(repo, "main moved")
    more = [
        (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO"),
        (lane(repo, tmp_path, "three", {"three.txt": "3\n"}), "T.THREE"),
    ]
    enqueue_all(env, more)
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    got = recorded(env)
    assert got["T.ONE"]["state"] == "red" and "ratchet" in got["T.ONE"]["why"], got
    assert got["T.TWO"]["state"] == "landed" and got["T.THREE"]["state"] == "landed", got
    assert gate_runs(env) == ["three"], gate_runs(env)


def test_a_stack_red_because_main_is_red_evicts_nothing(repo, tmp_path, ratchet_env):
    """Main itself red: no item's diff turned it, so none is evicted; the gate decides."""
    env = ratchet_env
    wts = [
        (lane(repo, tmp_path, "one", {"one.txt": "1\n"}), "T.ONE"),
        (lane(repo, tmp_path, "two", {"two.txt": "2\n"}), "T.TWO"),
    ]
    enqueue_all(env, wts)
    write(repo, "r-0.txt", "0\n")
    write(repo, "r-1.txt", "1\n")
    commit_all(repo, "main went red")
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert all("ratchet" not in got[i].get("why", "") for i in ("T.ONE", "T.TWO")), got
    assert gate_runs(env)[0] == "two", gate_runs(env)  # the stack was gated, as before
