"""A main move to the pre-step's own code is judged by its readers, not the set (GOH.SUITEONCEMISS).

`goh land-profile --last 24` (2026-11-21): the lander ran the whole pre-land again in 19 of 24
lands, and every one whose log names why said "<path> moved: the pre-step's own code" (14:
`.gatesrc`, `lib/pre_reuse.py`, `lib/reuse.py`, `lib/declared_reads.py`, `Cargo.lock`, ...),
450-850 s each, while the land gate beside it runs the tip's diff-chosen tests under the tip's
runner anyway (GOH.GATEPARALLEL; its carry takes nothing from the judged tree across such a move).
Now such a move asks the tip's selector for its set against the judged one (`within`, in the
enqueue's mode), runs what it adds, and judges every moved path by its readers -- the selection's
own rule for a lane changing the same files. Same stand-in table and helpers as test_pre_reuse.py.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_landq import env  # noqa: F401  -- the queue's fixture
from test_pre_reuse import JUDGED_SET, _history, _judged_set, _land, _main_moves  # noqa: F401

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pre_reuse


def _main(repo: Path, judged: str, tip: str, base: str, record: Path) -> tuple[int, list[str]]:
    """lib/pre_reuse.py's own answer: (exit, its lines), as tools/land.sh reads it."""
    out = io.StringIO()
    argv = ["--root", str(repo), "--judged", judged, "--tip", tip, "--base", base,
            "--record", str(record)]  # fmt: skip
    with redirect_stdout(out):
        rc = pre_reuse.main(argv)
    return rc, out.getvalue().splitlines()


def test_a_runner_move_whose_tip_set_the_verdict_judged_keeps_the_verdict(repo, tmp_path):
    """tools/pytest.sh: no test reads it here and the tip's set is the judged one: not run again."""
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "tools/pytest.sh", kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    assert (code, nodes) == (pre_reuse.REUSE, []), why
    assert "tools/pytest.sh moved: the pre-step's own code" in why and "is within" in why, why


@pytest.mark.parametrize("path", ["gates/_common.sh", "checks/check_x.py"])
def test_a_move_the_structural_gates_reach_runs_structural_not_the_set(repo, tmp_path, path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", path, kit=True)
    rc, lines = _main(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    assert rc == pre_reuse.NARROW and lines[1:] == [pre_reuse.STRUCTURAL], lines


def test_a_module_pytest_loads_before_every_test_is_judged_by_its_readers(repo, tmp_path):
    """The selection's rule for a base change (lib/test_closure.py `base`): its readers; the full
    gate for the rest -- which runs beside the pre-land and carries nothing across it."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "conftest.py").write_text("import sel_helper\n")
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    assert (code, nodes) == (pre_reuse.NARROW, ["tests/test_sel.py"]), why


@pytest.mark.parametrize(("main_text", "rerun"), [
    ("GOH_LAND_PRE='run it'\nGOH_LAND_SHARE=0.75\n", False),
    ("GOH_LAND_PRE='run it another way'\n", True),
])  # fmt: skip
def test_a_gatesrc_move_reruns_only_when_the_pre_steps_declaration_moved(
    repo, tmp_path, main_text, rerun
):
    """GOH.LANDSHARE's batch ran 851 s again for GOH_LAND_SHARE, a key the pre-step never reads."""
    (repo / ".gatesrc").write_text("GOH_LAND_PRE='run it'\n")
    judged, tip, base = _history(repo, tmp_path, "lane.txt", ".gatesrc", kit=True,
                                 main_text=main_text)  # fmt: skip
    code, why, _ = pre_reuse.decide(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    if rerun:
        assert code == pre_reuse.RERUN and "GOH_LAND_PRE moved in .gatesrc" in why, why
    else:
        assert code == pre_reuse.REUSE and ".gatesrc moved: the pre-step's own code" in why, why


def test_a_selector_move_that_adds_a_test_runs_it_beside_the_readers(repo, tmp_path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True,
                                 main_text='EXTRA = "tests/test_extra.py"\n')  # fmt: skip
    record = _judged_set(tmp_path, JUDGED_SET)
    rc, lines = _main(repo, judged, tip, base, record)
    assert rc == pre_reuse.NARROW, lines
    assert "adds 1 test the verdict did not judge, run too: tests/test_extra.py" in lines[0], lines
    assert lines[1:] == [f"{pre_reuse.ALSO} tests/test_extra.py", "tests/test_extra.py",
                         "tests/test_sel.py"], lines  # fmt: skip


def test_the_tip_set_is_asked_in_the_mode_the_judged_set_was_chosen_in(repo, tmp_path):
    """The enqueue skips a tree reader whose reads the diff misses (GOH.JUDGESBYREADS); the lander
    asked the tip in its own mode, and such readers read as tests the verdict never judged."""

    class Table:
        @staticmethod
        def affected(root, changed, enqueue=False):
            return {"tests/test_lane.py": "changed", **({} if enqueue else {"tests/test_t.py": ""})}

    _, tip, base = _history(repo, tmp_path, "lane.txt", "CHANGELOG.md")
    added, why = pre_reuse.within(repo, Table, tip, base, ["tests/test_lane.py"])
    assert added == [], why


def test_a_land_after_a_runner_move_runs_no_second_pre_land(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "tools/pytest.sh", kit=True,
                      record=str(_judged_set(tmp_path, JUDGED_SET)))  # fmt: skip
    assert seen == ["full"], log
    assert "tools/pytest.sh moved: the pre-step's own code" in log and "not run again" in log, log


def test_a_land_after_a_selector_move_that_adds_a_test_runs_it_and_the_readers(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
    monkeypatch,
) -> None:
    import test_pre_reuse

    def moves(repo: Path, name: str) -> None:
        (repo / name).write_text('EXTRA = "tests/test_extra.py"\n')
        git(repo, "commit", "-q", "--no-verify", "-am", f"main moves {name}")

    monkeypatch.setattr(test_pre_reuse, "_main_moves", moves)
    seen, log = _land(repo, tmp_path, env, "lib/sel_helper.py", kit=True,
                      record=str(_judged_set(tmp_path, JUDGED_SET)))  # fmt: skip
    assert seen == ["full", "narrow lib/sel_helper.py tests/test_extra.py"], log


def test_the_real_tree_judges_a_runner_move_by_its_readers():
    """goh's own tree: lib/reuse.py (GOH.FLAKYSCOPEDCACHE's whole re-run) is the pre-step's code,
    which some tests read; the structural gates do not reach it."""
    table = pre_reuse._table(REPO_ROOT)
    assert pre_reuse.pre_step_code(REPO_ROOT, table, ["lib/reuse.py"]) == ["lib/reuse.py"]
    assert table.readers(REPO_ROOT, ["lib/reuse.py"])
    assert pre_reuse.structural(REPO_ROOT, table, ["gates/_common.sh"])
