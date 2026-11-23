"""A land runs its pre-step once when main moved only where no test reads (GOH.PRELANDTWICE).

GOH.SPANFORGIVE's enqueue pre-step went green in 443 s at f31e3492, then the lander ran the whole
pre-land again (387 s, and 478 s more after main moved by the v0.137.0 release commit: CHANGELOG.md
and four deleted changelog/ fragments). The enqueue keeps its green verdict on the request, and
tools/land.sh reuses it -- or its own earlier run's -- when the moved paths are read by no test
(lib/pre_reuse.py, asking tools/affected_tests.py `readers`); a moved path some test reads runs
those tests alone, and a missing verdict runs the whole set.

Here the tree's reads table is a stand-in (READS in tests/_pre_reuse_kit.py): `read.txt` is read
by tests/test_r.py, gates/never_run.sh by tests/test_push.py, checks/cad_bambu.py by
tests/test_cad.py, nothing else by anything; its graph is the kit's own (lib/test_closure.py), so
what the fixture's pre-step code (KIT_TREE) reaches is read as the real table reads it. The real
table's answers are pinned last.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import _tree_graph
import pytest
from _pre_reuse_kit import JUDGED_SET, _history, _judged_set, _land, _with_table
from conftest import REPO_ROOT, git
from test_landq import env  # noqa: F401  -- `env` is the queue's fixture

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pre_reuse


def test_a_main_moved_only_where_no_test_reads_runs_no_second_pre_land(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "CHANGELOG.md")
    assert seen == ["full"], log  # the enqueue's run alone
    assert "not run again: 1 moved path, none read by a test: CHANGELOG.md" in log, log
    assert "the verdict the enqueue judged at" in log, log


def test_a_moved_path_a_test_reads_runs_its_readers_again(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "read.txt")
    assert seen == ["full", "narrow read.txt"], log
    assert "read by 1 test file: read.txt" in log, log


def test_a_land_whose_main_did_not_move_reuses_the_enqueue_verdict(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, None)
    assert seen == ["full"], log


def test_a_missing_enqueue_verdict_runs_the_whole_set_again(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "CHANGELOG.md", keep=False)
    assert seen == ["full", "full"], log
    assert "no verdict from the enqueue" in log, log


def test_a_path_both_sides_change_or_the_pre_steps_code_is_run_again(repo, tmp_path):
    judged, tip, base = _history(repo, tmp_path, "gates/x.sh", "pyproject.toml")
    code, why, _ = pre_reuse.decide(repo, judged, tip, base)
    assert code == pre_reuse.RERUN and "pyproject.toml moved" in why, why
    assert pre_reuse.decide(repo, judged, "0" * 40, base)[0] == pre_reuse.RERUN
    assert pre_reuse.decide(repo, judged, tip, "0" * 40)[0] == pre_reuse.RERUN
    assert pre_reuse.decide(repo, judged, judged, base)[0] == pre_reuse.REUSE  # the same tree


def test_a_merged_file_is_judged_by_what_reads_it(repo, tmp_path):
    """Measured from a base below main's move, the branch's own diff holds the moved file: a merged
    file, in neither judged tree. Its readers judge it (GOH.PRESTEPMOVED: GOH.LOADSOURCE's land ran
    the whole set again, 466 s, for docs/map.md, which 10 test files name); one no test reads keeps
    the verdict. The tree readers' verdict on its bytes is the full gate's, which runs them all."""
    judged, tip, _ = _history(repo, tmp_path, "lane.txt", "CHANGELOG.md")
    below = git(repo, "rev-parse", f"{judged}~1").strip()
    code, why, nodes = pre_reuse.decide(repo, judged, tip, below)
    assert (code, nodes) == (pre_reuse.REUSE, []) and "1 merged: CHANGELOG.md" in why, why
    assert pre_reuse.decide(repo, judged, tip, git(repo, "rev-parse", f"{tip}~1").strip())[0] == 0


def test_a_merged_file_a_test_reads_runs_its_readers(repo, tmp_path):
    judged, tip, _ = _history(repo, tmp_path, "lane.txt", "read.txt")
    below = git(repo, "rev-parse", f"{judged}~1").strip()
    code, why, nodes = pre_reuse.decide(repo, judged, tip, below)
    assert (code, nodes) == (pre_reuse.NARROW, ["tests/test_r.py"]), why
    assert "1 merged: read.txt" in why, why


@pytest.mark.parametrize(
    ("gate", "reader"),
    [("gates/never_run.sh", "tests/test_push.py"), ("checks/cad_bambu.py", "tests/test_cad.py")],
)
def test_a_gate_or_check_the_pre_step_never_runs_reruns_only_its_readers(
    repo, tmp_path, gate, reader
):
    """GOH.PRESTEPMOVED: every gates/ and checks/ path was the pre-step's own code, so main moving
    a gate it never runs (push gating: GOH.COVOFFLAND's land, ~17 min) or checks/cad_bambu.py
    (GOH.LOADSOURCE's, 607 s) ran the whole set again. Neither is reached by what chooses the set
    or the structural gates it runs: their readers judge them."""
    judged, tip, base = _history(repo, tmp_path, "lane.txt", gate, kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base)
    assert (code, nodes) == (pre_reuse.NARROW, [reader]), why


@pytest.mark.parametrize(
    "path", ["gates/_common.sh", "checks/check_x.py", "lib/sel_helper.py", "tools/pytest.sh"]
)
def test_a_move_to_the_pre_steps_own_code_with_no_judged_set_runs_it_all_again(
    repo, tmp_path, path
):
    """What the pre-step runs is read from its own sources: a file the structural gates source, a
    check the binary runs, the selection's own lib/ module (which the prefix rule never saw), the
    pytest entry. With no judged set to hold the tip's against, each runs the whole set again
    (with one, its readers: tests/test_pre_reuse_runner.py, GOH.SUITEONCEMISS)."""
    judged, tip, base = _history(repo, tmp_path, "lane.txt", path, kit=True)
    code, why, _ = pre_reuse.decide(repo, judged, tip, base)
    assert code == pre_reuse.RERUN and f"{path} moved: the pre-step's own code" in why, why


def test_the_real_pre_steps_code_is_what_it_runs_not_a_directory():
    """goh's own tree: the selection, the pytest entry, the structural gates and what they source or
    run, and the binary's build inputs are the pre-step's code; push gating, the CAD scanner and
    docs/map.md are not (GOH.PRESTEPMOVED)."""
    table = pre_reuse._table(REPO_ROOT)
    moved = ["gates/push_gate.sh", "checks/cad_bambu.py", "docs/map.md", "tools/affected_tests.py",
             "lib/test_closure.py", "gates/_common.sh", "checks/check_file_length.py",
             "crates/goh/src/length.rs", ".gatesrc"]  # fmt: skip
    got = pre_reuse.pre_step_code(REPO_ROOT, table, moved)
    assert got == moved[3:], got


def test_a_tree_with_no_reads_table_is_run_again(repo, tmp_path):
    judged, _, base = _history(repo, tmp_path, "lane.txt", "CHANGELOG.md")
    git(repo, "rm", "-q", "tools/affected_tests.py")
    git(repo, "commit", "-q", "--no-verify", "-m", "no table")
    code, why, _ = pre_reuse.decide(repo, judged, git(repo, "rev-parse", "HEAD").strip(), base)
    assert code == pre_reuse.RERUN and "tools/affected_tests.py" in why, why


def test_the_real_table_reads_no_release_commits_version_or_fragments():
    """goh's own table on its own tree: a release commit's VERSION and changelog/ fragments are read
    by no test; CHANGELOG.md is named by tests/test_changelog_fragments.py alone (its fixture's
    file name), so a release costs that one file, never the set."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.pop(0)
    gone = "GOH" + ".GONE.md"  # spelled apart: a literal would name it here
    moved = ["VERSION", f"changelog/{gone}", "CHANGELOG.md"]
    got = affected_tests.readers(REPO_ROOT, moved, graph=_tree_graph.real())
    assert set(got) <= {"tests/test_changelog_fragments.py"}, got
    head = ["--judged", "HEAD", "--tip", "HEAD", "--base", "HEAD", "--root", str(REPO_ROOT)]
    out = subprocess.run([sys.executable, str(REPO_ROOT / "lib" / "pre_reuse.py"), *head],
                         capture_output=True, text=True, check=False,
                         stdin=subprocess.DEVNULL)  # fmt: skip
    assert out.returncode == 0 and "the tree judged at HEAD" in out.stdout, out.stdout + out.stderr


def test_reading_a_tips_table_leaves_the_callers_modules_its_own(repo, tmp_path):
    """GOH.CIRED0136: `_table` left the tip's `affected_tests` (and its `lib`) in sys.modules, so a
    suite worker that ran this file judged later tests with the stand-in: tests/_land_verdicts.py's
    `affected_tests.affected` was an AttributeError on CI at v0.138.0."""
    _with_table(repo, tmp_path)
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import affected_tests
    finally:
        sys.path.pop(0)
    before = {m: sys.modules[m] for m in ("affected_tests", "lib") if m in sys.modules}
    table = pre_reuse._table(repo)
    assert table is not None and table is not affected_tests and hasattr(table, "READ")
    assert {m: sys.modules.get(m) for m in before} == before
    assert hasattr(sys.modules["affected_tests"], "affected")


# THE SELECTION'S OWN CODE (GOH.PRESTEPSELFMOVE): GOH.LANEPRESTEP's land ran its whole set
# again, 402 s, because main moved lib/binary_runners.py (41f73575, a cache: the same set). What
# chooses the set is asked again on the tip, against the set the verdict judged (`chosen`).
def test_a_selector_move_whose_tip_set_the_verdict_judged_runs_only_its_readers(repo, tmp_path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True)
    record = _judged_set(tmp_path, [*JUDGED_SET, "tests/test_lane.py"])
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base, record)
    assert (code, nodes) == (pre_reuse.NARROW, ["tests/test_sel.py"]), why
    assert "lib/sel_helper.py moved: the pre-step's own code" in why, why
    assert "the tip's set (1 test) is within the 2 judged" in why, why


def test_a_whole_file_the_verdict_judged_only_some_tests_of_is_an_added_test(repo, tmp_path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True,
                                 main_text='EXTRA = "tests/test_walk.py"\n')  # fmt: skip
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    assert code == pre_reuse.NARROW and "adds 1 test" in why and "tests/test_walk.py" in nodes, why
    record = _judged_set(tmp_path, ["tests/test_walk.py"])  # the whole file judged: its nodes too
    assert pre_reuse.decide(repo, judged, tip, base, record)[2] == ["tests/test_sel.py"]


@pytest.mark.parametrize("chosen", [None, "no record"])
def test_a_selector_move_with_no_judged_set_recorded_runs_the_whole_set(repo, tmp_path, chosen):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True)
    record = _judged_set(tmp_path, None) if chosen is None else tmp_path / "absent.json"
    code, why, _ = pre_reuse.decide(repo, judged, tip, base, record)
    assert code == pre_reuse.RERUN and "lib/sel_helper.py moved: the pre-step's own code" in why
    assert "the set the verdict judged is not recorded" in why, why


def test_a_selector_module_pytest_loads_only_through_the_selector_is_judged_by_its_readers(
    repo, tmp_path
):
    """LANEPRESTEP's lib/binary_runners.py is in pytest's base only through tests/_land_verdicts.py,
    which imports tools/affected_tests.py: judged by its readers (one conftest imports itself is
    too: tests/test_pre_reuse_runner.py, GOH.SUITEONCEMISS)."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "conftest.py").write_text("import _lv\n")
    (repo / "tests" / "_lv.py").write_text("def choose():\n    import affected_tests\n")
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "lib/sel_helper.py", kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base, _judged_set(tmp_path, JUDGED_SET))
    assert (code, nodes) == (pre_reuse.NARROW, ["tests/test_sel.py"]), why


def test_the_land_hands_the_judged_set_to_the_decision(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    """tools/land.sh passes the kept record (lib/land_pre_reuse.sh `--record`): a selector move
    whose tip set the enqueue judged is decided NARROW (the moved file's readers, not the set),
    which the gate's one suite judges: the lander runs no pre-land of its own (GOH.LANDONERUN)."""
    record = _judged_set(tmp_path, JUDGED_SET)
    seen, log = _land(repo, tmp_path, env, "lib/sel_helper.py", kit=True, record=str(record))
    assert seen == ["full"], log
    assert "but what the moved paths' readers judge" in log, log
    assert "not run -- the gate's one suite judges them" in log, log
    assert "the pre-step's own code, and the tip's set" in log, log


def test_the_record_names_the_set_it_judged(tmp_path):
    record = tmp_path / "rec.json"
    record.write_text(json.dumps({"passed": {}}))
    pre_reuse.stamp(str(record), "b" * 40, ["tests/test_b.py", "tests/test_a.py::t"])
    got = json.loads(record.read_text())
    assert got["chosen"] == ["tests/test_a.py::t", "tests/test_b.py"], got
