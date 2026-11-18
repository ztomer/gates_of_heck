"""A red batch's halves gate AT ONCE (GOH.LANDBISECTPAR, lib/landq_spec.py).

The batch (GOH.LANDBATCH) bisected a red stack serially: the first half on main, then the second
on whatever landed, one full land gate after the other (SELECTAUDIT spent ~45 min so on 2026-11-17
in a 2-item batch). Now the second half is stacked on main as well and gated in the second gate
slot beside the first: when the first half lands nothing, that run IS the gate the serial queue
would have run next, and it lands with no further gate; when anything of the first half lands,
main is not the base it judged, so it is discarded -- killed if still gating, never fast-forwarded
if it was green (land.sh holds a speculative green for the batch's word).

No sleeps: the gates meet on a fifo the test holds open. The first half's gate waits (bounded) for
the second half's gate to write to it, so `met` is written only when the two runs OVERLAP -- the
bisection takes max(gate), not the sum. Serially the wait runs out and nothing is met.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import hermetic_env
from test_landq_batch import FAKE_LOOPCTL, enqueue_all, gate_runs, landq, lane, on_main, recorded
from timing_bounds import patience

# Each run logs its tip's subject; X is the first half (subject `one`), Y the second (`two`).
GATE = r"""
s=$(git log -1 --format=%s); echo "$s" >>"$GATE_LOG"
case "$CASE" in
  xred)   # X carries bad.txt; Y alone is green
    if [ "$s" = one ]; then read -r -t "$WAIT" g <"$BARRIER" && echo "met $g" >>"$MET"; exit 1; fi
    if [ -e bad.txt ]; then exit 1; fi
    echo "$s" >"$BARRIER"; exit 0 ;;
  yred)   # Y carries bad.txt; Y alone on main blocks until it is killed
    if [ -e bad.txt ] && [ ! -e one.txt ]; then
      read -r -t "$WAIT" got <"$BARRIER"; echo "spec-finished" >>"$MET"; exit 1; fi
    if [ -e bad.txt ]; then exit 1; fi
    exit 0 ;;
  clash)  # each is green alone, the two together are red
    if [ -e one.txt ] && [ -e two.txt ]; then exit 1; fi
    if [ "$s" = one ]; then read -r -t "$WAIT" g <"$BARRIER" && echo "met $g" >>"$MET"; exit 0; fi
    echo "$s" >"$BARRIER"; exit 0 ;;
esac
exit 2
"""


@pytest.fixture
def par(tmp_path: Path):
    fake, barrier = tmp_path / "loopctl.py", tmp_path / "barrier"
    fake.write_text(FAKE_LOOPCTL)
    os.mkfifo(barrier)
    held = os.open(barrier, os.O_RDWR)  # a reader and a writer: no open blocks, no line lost
    yield hermetic_env(
        drop_git=True, GOH_LANDQ_DIR=str(tmp_path / "landq"), GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"), GOH_LAND_TIMEOUT=str(int(patience(300))),
        GOH_LAND_GATE=GATE, GATE_LOG=str(tmp_path / "gate.log"), BARRIER=str(barrier),
        MET=str(tmp_path / "met"), WAIT=str(int(patience(20))), GOH_LANDQ_BATCH="2",
    )  # fmt: skip
    os.close(held)


def met(env: dict[str, str]) -> list[str]:
    path = Path(env["MET"])
    return path.read_text().splitlines() if path.exists() else []


def two(repo: Path, tmp_path: Path, x: dict[str, str], y: dict[str, str]) -> list[tuple[Path, str]]:
    return [(lane(repo, tmp_path, "one", x), "T.ONE"), (lane(repo, tmp_path, "two", y), "T.TWO")]


@pytest.mark.load_sensitive(reason="the two gates meet within a bounded read -t")
def test_a_red_first_half_and_the_second_on_main_gate_at_once(repo, tmp_path, par):
    env = {**par, "CASE": "xred"}
    enqueue_all(env, two(repo, tmp_path, {"one.txt": "1\n", "bad.txt": "x\n"}, {"two.txt": "2\n"}))
    done = landq(env, "--drain")
    assert done.returncode == 0, done.stdout + done.stderr
    assert met(env) == ["met two"], (met(env), gate_runs(env))  # max(gate): the two runs overlapped
    got = recorded(env)
    assert got["T.ONE"]["state"] == "red" and got["T.TWO"]["state"] == "landed", got
    assert on_main(repo, got["T.TWO"]["sha"]) and not (repo / "bad.txt").exists()
    assert sorted(gate_runs(env)) == ["one", "two", "two"], gate_runs(env)  # stack, X, Y: no 4th
    assert got["T.TWO"]["batch"]["gates"] == 3, got


@pytest.mark.load_sensitive(reason="a run not killed waits out a bounded read -t")
def test_a_green_first_half_kills_the_second_halfs_run_on_old_main(repo, tmp_path, par):
    env = {**par, "CASE": "yred", "WAIT": str(int(patience(120)))}
    enqueue_all(env, two(repo, tmp_path, {"one.txt": "1\n"}, {"two.txt": "2\n", "bad.txt": "y\n"}))
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.TWO"]["state"] == "red", got
    assert "spec-finished" not in met(env), met(env)  # killed, not waited out
    assert on_main(repo, got["T.ONE"]["sha"]) and not (repo / "bad.txt").exists()
    assert "batch " in Path(got["T.TWO"]["log"]).read_text(), got  # its own log, naming the run


@pytest.mark.load_sensitive(reason="the two gates meet within a bounded read -t")
def test_a_speculative_green_on_a_base_main_left_is_never_landed(repo, tmp_path, par):
    env = {**par, "CASE": "clash"}
    enqueue_all(env, two(repo, tmp_path, {"one.txt": "1\n"}, {"two.txt": "2\n"}))
    assert landq(env, "--drain").returncode == 0
    assert met(env) == ["met two"], (met(env), gate_runs(env))  # Y's green came while X gated
    got = recorded(env)
    assert got["T.ONE"]["state"] == "landed" and got["T.TWO"]["state"] == "red", got
    assert (repo / "one.txt").exists() and not (repo / "two.txt").exists()  # held, then dropped
    assert got["T.TWO"]["batch"]["gates"] == 3, got  # Y on X is the stack's tree: red, no 4th
