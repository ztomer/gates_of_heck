"""A land runs its pre-step once when main moved only where no test reads (GOH.PRELANDTWICE).

GOH.SPANFORGIVE's enqueue pre-step went green in 443 s at f31e3492, then the lander ran the whole
pre-land again (387 s, and 478 s more after main moved by the v0.137.0 release commit: CHANGELOG.md
and four deleted changelog/ fragments). The enqueue keeps its green verdict on the request, and
tools/land.sh reuses it -- or its own earlier run's -- when the moved paths are read by no test
(lib/pre_reuse.py, asking tools/affected_tests.py `readers`); a moved path some test reads runs
those tests alone, and a missing verdict runs the whole set.

Here the tree's reads table is a stand-in (READS below): `read.txt` is read by tests/test_r.py,
gates/push_gate.sh by tests/test_push.py, checks/cad_bambu.py by tests/test_cad.py, nothing else
by anything; its graph is the kit's own (lib/test_closure.py), so what the fixture's pre-step code
(KIT_TREE) reaches is read as the real table reads it. The real table's answers are pinned last.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_landq import _land_wt, env, landq, recorded  # noqa: F401  -- `env` is the queue's fixture

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pre_reuse

READS = """\
import sys
sys.path.insert(0, {kit!r})
from lib.test_closure import Closure  # the kit's graph: what the pre-step's code reaches
del sys.path[0]
try:
    import sel_helper  # noqa: F401 -- the selection's own lib/ module, when the tree has one
except ImportError:
    pass
READ = {{"read.txt": "tests/test_r.py", "gates/push_gate.sh": "tests/test_push.py",
        "checks/cad_bambu.py": "tests/test_cad.py"}}
def readers(root, changed, tests=None):
    return {{READ[p]: "reads " + p for p in changed if p in READ}}
if __name__ == "__main__":
    with open({seen!r}, "a") as f:
        f.write("narrow " + " ".join(sys.argv[sys.argv.index("--changed") + 1 :]) + "\\n")
"""


# The pre-step's own code in small (GOH.PRESTEPMOVED): the structural gates source _common.sh and
# the binary runs checks/check_*.py; the selection imports lib/sel_helper.py. push_gate.sh and the
# CAD scanner are gates and checks the pre-step never runs.
KIT_TREE = {
    "gates/structural.sh": '. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"\n',
    "gates/_common.sh": "x=1\n",
    "gates/push_gate.sh": "echo push\n",
    "checks/check_x.py": "print(1)\n",
    "checks/cad_bambu.py": "print(2)\n",
    "lib/sel_helper.py": "X = 1\n",
    "tools/pytest.sh": "exit 0\n",
}


def _with_table(repo: Path, tmp_path: Path, kit: bool = False) -> Path:
    seen = tmp_path / "seen"
    (repo / "tools").mkdir(exist_ok=True)
    (repo / "tools" / "affected_tests.py").write_text(
        READS.format(seen=str(seen), kit=str(REPO_ROOT))
    )
    for name in ("read.txt", "CHANGELOG.md"):
        (repo / name).write_text("main's first\n")
    for name, text in KIT_TREE.items() if kit else ():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "a reads table")
    return seen


def _main_moves(repo: Path, name: str) -> None:
    (repo / name).write_text("main moved it\n")
    git(repo, "commit", "-q", "--no-verify", "-am", f"main moves {name}")


def _land(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811
    moves: str | None,
    keep: bool = True,
):
    seen = _with_table(repo, tmp_path)
    wt, _ = _land_wt(repo, tmp_path)
    env = {**env, "GOH_LAND_GATE": "true", "GOH_LAND_PRE": f"echo full >> {seen}"}
    done = landq(env, "--enqueue", "--land", str(wt), "--item", "T.T", "--no-start")
    assert done.returncode == 0, done.stdout + done.stderr
    if moves:
        _main_moves(repo, moves)
    if not keep:
        for kept in (tmp_path / "landq" / "pre").iterdir():
            kept.unlink()
    assert landq(env, "--drain").returncode == 0
    got = recorded(env)["T.T"]
    assert got["state"] == "landed", Path(got["log"]).read_text()
    return seen.read_text().splitlines(), Path(got["log"]).read_text()


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


# The decision itself, on a two-commit history: what moved between the judged tip and the new one.
def _history(
    repo: Path, tmp_path: Path, judged_adds: str, main_moves: str, kit: bool = False
) -> tuple[str, ...]:
    _with_table(repo, tmp_path, kit)
    base0 = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "lane")
    (repo / judged_adds).parent.mkdir(parents=True, exist_ok=True)
    (repo / judged_adds).write_text("the lane's\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "lane")
    judged = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", base0)
    (repo / main_moves).parent.mkdir(parents=True, exist_ok=True)
    (repo / main_moves).write_text("main's\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "main")
    base = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "checkout", "-q", "lane")
    git(repo, "rebase", "-q", base)
    return judged, git(repo, "rev-parse", "HEAD").strip(), base


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
    [("gates/push_gate.sh", "tests/test_push.py"), ("checks/cad_bambu.py", "tests/test_cad.py")],
)
def test_a_gate_or_check_the_pre_step_never_runs_reruns_only_its_readers(
    repo, tmp_path, gate, reader
):
    """GOH.PRESTEPMOVED: every gates/ and checks/ path was the pre-step's own code, so main moving
    gates/push_gate.sh (GOH.COVOFFLAND's land, ~17 min) or checks/cad_bambu.py (GOH.LOADSOURCE's,
    607 s) ran the whole set again. Neither is reached by what chooses the set or the structural
    gates it runs: their readers judge them."""
    judged, tip, base = _history(repo, tmp_path, "lane.txt", gate, kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base)
    assert (code, nodes) == (pre_reuse.NARROW, [reader]), why


@pytest.mark.parametrize(
    "path", ["gates/_common.sh", "checks/check_x.py", "lib/sel_helper.py", "tools/pytest.sh"]
)
def test_a_move_to_the_pre_steps_own_code_runs_it_all_again(repo, tmp_path, path):
    """What the pre-step runs is read from its own sources: a file the structural gates source, a
    check the binary runs, the selection's own lib/ module (which the prefix rule never saw), the
    pytest entry. Each still runs the whole set again."""
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
    got = affected_tests.readers(REPO_ROOT, moved)
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
