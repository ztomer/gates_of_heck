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
from types import SimpleNamespace

import land_check
import landq_ratchet
import pytest
from conftest import commit_all, git, hermetic_env, write
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


# Which item's own diff did it (GOH.PYSLOW4: a drain per answer before; the one above is kept).


def _batch(repo: Path, tmp_path: Path, said: list[str]) -> SimpleNamespace:
    return SimpleNamespace(id="b", main=repo, q=tmp_path / "q", stack=lambda live: live,
                           git=lambda d, *a: git(d, *a).strip(),
                           kit=SimpleNamespace(say=said.append))  # fmt: skip


def _items(*names: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(req={"worktree": n, "repo": "/r"}, tip=n * 8) for n in names]


@pytest.mark.parametrize(
    ("tips", "main", "found"),
    [
        ([True, True], True, 0),  # the lowest item, over a green main: its own diff
        ([True, True], False, None),  # main red too: no item's
        ([False, True], False, 1),  # red over a green item: main is not asked
        ([False, False], False, 2),  # green below the top, which is the red one
        ([None, True], True, None),  # no verdict: nobody is named
    ],
)
def test_the_lowest_tip_red_over_a_green_one_names_its_item(monkeypatch, tips, main, found):
    items = _items("one", "two", "three")
    reds = {Path(it.req["worktree"]): red for it, red in zip(items, tips, strict=False)}
    monkeypatch.setattr(landq_ratchet, "red_on", lambda root, _refused: reds[root])
    monkeypatch.setattr(landq_ratchet, "main_green", lambda *_: main)
    got = landq_ratchet.find(None, items, ["x"])
    assert got is (None if found is None else items[found])


def test_main_is_judged_in_a_checkout_of_its_own(repo, tmp_path, monkeypatch):
    """The head item's question, asked of main as it stands; the checkout is gone after."""
    monkeypatch.setenv("GOH_LAND_CHECK_STEPS", COUNT)
    b = _batch(repo, tmp_path, [])
    write(repo, "r-0.txt", "0\n")
    commit_all(repo, "main moved")
    assert landq_ratchet.main_green(b, [COUNT]) is True
    write(repo, "r-1.txt", "1\n")
    commit_all(repo, "main went red")
    assert landq_ratchet.main_green(b, [COUNT]) is False
    assert list((b.q / "ratchet-main").iterdir()) == []
    assert "ratchet-main" not in git(repo, "worktree", "list")


def test_a_stack_red_because_main_is_red_evicts_nothing(repo, tmp_path, monkeypatch):
    """Main itself red: no item's diff turned it, so none is evicted; the gate decides."""
    said: list[str] = []
    monkeypatch.setattr(land_check, "run", lambda *_a, **_k: ["the count"])
    monkeypatch.setattr(landq_ratchet, "red_on", lambda *_: True)
    monkeypatch.setattr(landq_ratchet, "main_green", lambda *_: False)
    items = _items("one", "two")
    assert landq_ratchet.first(_batch(repo, tmp_path, said), items) == items
    assert "the gate decides" in said[-1], said
