"""A red idle full suite on main, told to every enqueue (GOH.MAINREDNOTICE, lib/main_red.py).

The idle suite found main c58986b0 red at 22:19Z and nothing told the enqueues; lanes went red on
it at their gates. With a red idle verdict on a commit main holds, the enqueue says so and the
failing nodes join its pre-step's set; a green verdict clears it; a run that is no verdict (it
yielded to a land) keeps it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT, git, write
from test_landq import _land_wt, landq
from test_landq_background import env  # noqa: F401 -- `env` is a fixture

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import landq_state
import main_red

NODE = "tests/test_t.py::test_reads_the_live_queue"


def _idle(q: Path, record: dict, log: str = "") -> None:
    (q / "idle-suite").mkdir(parents=True, exist_ok=True)
    landq_state.write(q / "idle-suite" / "last.json", record)
    (q / "logs").mkdir(parents=True, exist_ok=True)
    (q / "logs" / f"idle-suite-{record['sha']}.log").write_text(log)


def test_the_red_is_kept_across_a_run_that_is_no_verdict_and_cleared_by_a_green():
    assert main_red.carry({}, "a" * 40, "red") == "a" * 40
    red = {"sha": "a" * 40, "outcome": "red"}
    assert main_red.carry(red, "b" * 40, "yielded") == "a" * 40
    assert main_red.carry({"sha": "b" * 40, "outcome": "deferred", "red": "a" * 40}, "c", "failed")
    assert main_red.carry({"red": "a" * 40}, "b" * 40, "green") == ""


def test_a_red_idle_verdict_on_main_is_said_and_its_nodes_go_to_the_pre_step(repo, tmp_path, env):  # noqa: F811
    q = Path(env["GOH_LANDQ_DIR"])
    tip = git(repo, "rev-parse", "HEAD").strip()
    _idle(q, {"sha": tip, "outcome": "red", "at": "22:19Z", "green": ""},
          f"FAILED {NODE} - AssertionError\n")  # fmt: skip
    assert main_red.red(q, str(repo))["nodes"] == [NODE]
    assert main_red.red(q, str(tmp_path)) is None  # another repo holds no such tip
    wt, _ = _land_wt(repo, tmp_path)
    seen = tmp_path / "pre-env"
    pre = f'printf %s "${main_red.ENV}" > {seen}'
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": pre, "GOH_LAND_CHECK_STEPS": "true"}
    done = landq(
        env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.MR", "--no-start"
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert f"main {tip[:8]} is red in the idle full suite" in out and NODE in out, out
    assert seen.is_file() and seen.read_text() == NODE, out
    _idle(q, {"sha": tip, "outcome": "green", "at": "23:00Z", "green": tip})
    assert main_red.red(q, str(repo)) is None


def test_the_named_nodes_the_tree_still_defines_join_the_chosen_set(tmp_path, monkeypatch):
    write(tmp_path, "tests/test_t.py", "def test_reads_the_live_queue():\n    pass\n")
    gone = "tests/test_t.py::test_renamed_since"
    monkeypatch.setenv(main_red.ENV, f"{NODE}[a]\n{gone}\ntests/test_gone.py::test_x")
    assert list(main_red.also(tmp_path)) == [f"{NODE}[a]"]
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import affected_tests

    chosen = affected_tests.affected(tmp_path, [], enqueue=False)
    assert f"{NODE}[a]" in chosen, chosen
