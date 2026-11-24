"""The pre-step judges the whole tree FIRST, apart from the diff's tests (GOH.PREPARITY).

10 of the 23 red lands of 2026-11-15..17 were a structural gate or a repo-wide judge (a test that
reads every file or key) the pre-step either never ran (the 500-line cap: AFFECTEDBINARY) or ran
inside one suite with the diff's tests, which reached the 600 s deadline at load 175 with no
verdict (ADAPTLANES: the enqueue queued it, the lander's pre-step went red on two judges 444 s
later). So `affected_tests.py --run --structural` runs `gates/structural.sh --full`, then the
judges, then the rest: a red judge is a red verdict before the slow tests start.
"""

from __future__ import annotations

import sys
from pathlib import Path

import _tree_graph
import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_landq import FAKE_LOOPCTL, _land_wt, _pending, landq

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

PRE = f'"$GOH_PY" {REPO_ROOT}/tools/affected_tests.py --run --structural --base "$1"'
PYTEST = '#!/usr/bin/env bash\nexec "$GOH_PY" -m pytest -q -p no:cacheprovider "$@"\n'
SLOW = "import time\n\n\ndef test_slow():  # names lib/new.py\n    time.sleep(60)\n"
# The planted judges take `tmp_path`: the enqueue's own check runs every plain rule by its shape
# (lib/rule_tests.py, GOH.RULESLEFT) and refuses a plain one before any pre-step, which
# test_a_plain_rules_red_is_refused_by_the_check_before_any_pre_step pins. A judge the check cannot
# see by its shape is the pre-step's to find, and these tests are the pre-step's.
MAP_JUDGE = """from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_every_script_has_a_row_in_the_map(tmp_path):
    text = (REPO_ROOT / "docs" / "map.md").read_text()
    missing = [p.name for p in (REPO_ROOT / "lib").glob("*.py") if f"`{p.name}`" not in text]
    assert not missing, f"no row in docs/map.md for: {missing}"
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A queue of the test's own; --no-start, so no lander runs."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
    )


def _branch(repo: Path, tmp_path: Path, files: dict[str, str]) -> Path:
    wt, _ = _land_wt(repo, tmp_path)
    write_gatesrc(wt, "GOH_ROADMAP=none\nGOH_MAX_LINES=500\n")
    for rel, text in files.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "--no-verify", "-m", "branch")
    return wt


def _enqueue(env: dict[str, str], wt: Path, **extra: str):
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": PRE, **extra}
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)  # the fixture's own suite is no nested goh suite
    return landq(env, "--enqueue", "--foreground", "--land", str(wt), "--item", "T.J", "--no-start")


def test_a_branch_with_a_501_line_file_is_refused_at_enqueue(repo, tmp_path, env) -> None:
    long = "".join(f"x{i} = {i}\n" for i in range(501))
    wt = _branch(repo, tmp_path, {"long.py": long})
    done = _enqueue(env, wt)
    out = done.stdout + done.stderr
    assert done.returncode == 2 and "500-line cap" in out and "nothing enqueued" in out, out
    assert _pending(tmp_path) == []


KEY_JUDGE = """import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_every_internal_key_is_on_the_list(tmp_path):
    listed = (REPO_ROOT / "docs" / "internal_keys.txt").read_text().split()
    texts = [p.read_text() for p in (REPO_ROOT / "lib").glob("*.py")]
    keys = {k for t in texts for k in re.findall(r"GOH_[A-Z_]+", t)}
    assert keys <= set(listed), f"internal keys on no list: {sorted(keys - set(listed))}"
"""
# (the judge, its file, what the branch plants, the line its failure prints)
PLANTED = {
    "map-row": ("tests/test_wiring.py", MAP_JUDGE, {"docs/map.md": "| `old.py` | a row |\n",
                "lib/old.py": ""}, "no row in docs/map.md for: ['new.py']"),
    "internal-key": ("tests/test_verdict_free_keys.py", KEY_JUDGE, {
        "docs/internal_keys.txt": "GOH_OLD\n", "lib/old.py": 'K = "GOH_OLD"\n'},
        "internal keys on no list: ['GOH_UNDOCUMENTED']"),
}  # fmt: skip


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_a_judge_s_red_is_refused_at_enqueue_though_the_rest_reaches_its_deadline(
    repo, tmp_path, env, case
) -> None:
    """The judge fails in seconds; the diff's slow test would hold the run past the deadline,
    and a deadline is no verdict: before the judges ran first, this tip was QUEUED."""
    judge, text, planted, says = PLANTED[case]
    files = {**planted, "tools/pytest.sh": PYTEST, "tests/test_a_slow.py": SLOW, judge: text}
    files["lib/new.py"] = 'K = "GOH_UNDOCUMENTED"\n' if case == "internal-key" else ""
    wt = _branch(repo, tmp_path, files)
    done = _enqueue(env, wt, GOH_LAND_PRE_MAX="40s")
    out = done.stdout + done.stderr
    assert done.returncode == 2 and says in out and "nothing enqueued" in out, out
    assert f"{judge}::test_every_" in out, out
    assert _pending(tmp_path) == []


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_a_plain_rules_red_is_refused_by_the_check_before_any_pre_step(
    repo, tmp_path, env, case
) -> None:
    """The same judge as a plain function is a rule by its shape: the enqueue's check refuses it
    (exit 1, recorded red, nothing queued) and no pre-step starts."""
    judge, text, planted, says = PLANTED[case]
    plain = text.replace("(tmp_path):", "():")
    assert plain != text
    files = {**planted, "tools/pytest.sh": PYTEST, judge: plain}
    files["lib/new.py"] = 'K = "GOH_UNDOCUMENTED"\n' if case == "internal-key" else ""
    wt = _branch(repo, tmp_path, files)
    started = tmp_path / "pre-step-started"
    done = _enqueue(env, wt, GOH_LAND_PRE=f"touch {started}")
    out = done.stdout + done.stderr
    assert done.returncode == 1 and says in out and "nothing queued" in out, out
    assert f"{judge}::test_every_" in out, out
    assert not started.exists()
    assert _pending(tmp_path) == []


# The judges of goh's own tree that went red in a land the pre-step had passed or never judged
# (2026-11-17): each must be chosen, and run in the FIRST phase, for any change.
JUDGES = (
    "tests/test_verdict_free_keys.py::test_every_internal_key_is_on_the_list",
    "tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
    "tests/test_suite_drift.py::test_no_new_pool_sized_to_the_machine",
    "tests/test_config_schema.py::test_config_schema_covers_keys",
    "tests/test_gate_temp_leaks.py::test_structural_full_leaves_nothing_in_tmpdir",
    "tests/test_self_walk.py",
)


def test_goh_s_own_judges_run_first_for_any_change() -> None:
    chosen = at.affected(REPO_ROOT, ["lib/a_new_module.py"], graph=_tree_graph.real())
    judges, rest = at.phases(REPO_ROOT, chosen)
    # a judge chosen whole (a test reading a changed directory, lib/declared_reads.py) runs too
    missing = [j for j in JUDGES if j not in judges and j.split("::")[0] not in judges]
    assert not missing, missing
    assert not set(judges) & set(rest)


def test_the_judges_are_found_by_what_they_read_and_the_hand_list_only_shrinks() -> None:
    """A ratchet: ALWAYS (readers no pattern sees) holds two files and may only lose them; the
    judges found on an empty diff never fall under the floor (a detector that lost sight of
    them reads as a pre-step with nothing to judge). Raise FLOOR when it rises a lot."""
    floor = 330
    assert len(at.ALWAYS) <= 2, at.ALWAYS
    judges, _ = at.phases(REPO_ROOT, at.affected(REPO_ROOT, [], graph=_tree_graph.real()))
    assert len(judges) >= floor, (len(judges), floor)


def test_the_judges_record_and_the_rests_are_one_record_of_the_same_tree(tmp_path) -> None:
    """tools/land.sh hands the gate ONE record (GOH.LANDPREDUP): the judges' run is joined into
    the rest's when both judged the same tree and tip, and never joined across trees."""
    import json

    first, record = tmp_path / "rec.judges", tmp_path / "rec"
    tree = {"identity": "i", "sha": "s"}
    first.write_text(json.dumps({**tree, "passed": {"tests/test_j.py::test_every_x": False}}))
    record.write_text(json.dumps({**tree, "passed": {"tests/test_r.py::test_y": True}}))
    at._merge(record, first)
    got = json.loads(record.read_text())["passed"]
    assert got == {"tests/test_j.py::test_every_x": False, "tests/test_r.py::test_y": True}
    assert not first.exists()
    first.write_text(json.dumps({"identity": "other", "sha": "s", "passed": {"a": False}}))
    at._merge(record, first)
    assert json.loads(record.read_text())["passed"] == got and not first.exists()
