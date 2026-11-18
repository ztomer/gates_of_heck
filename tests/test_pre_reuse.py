"""A land runs its pre-step once when main moved only where no test reads (GOH.PRELANDTWICE).

GOH.SPANFORGIVE's enqueue pre-step went green in 443 s at f31e3492, then the lander ran the whole
pre-land again (387 s, and 478 s more after main moved by the v0.137.0 release commit: CHANGELOG.md
and four deleted changelog/ fragments). The enqueue keeps its green verdict on the request, and
tools/land.sh reuses it -- or its own earlier run's -- when the moved paths are read by no test
(lib/pre_reuse.py, asking tools/affected_tests.py `readers`); a moved path some test reads runs
those tests alone, and a missing verdict runs the whole set.

Here the tree's reads table is a stand-in (READS below): `read.txt` is read by tests/test_r.py,
nothing else is read by anything. The real table's answer for a release commit is pinned last.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git
from test_landq import _land_wt, env, landq, recorded  # noqa: F401  -- `env` is the queue's fixture

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pre_reuse

READS = """\
import sys
READ = {{"read.txt": "tests/test_r.py"}}
class Closure:
    def __init__(self, root): pass
    def base(self): return set()
def readers(root, changed, tests=None):
    return {{READ[p]: "reads " + p for p in changed if p in READ}}
if __name__ == "__main__":
    with open({seen!r}, "a") as f:
        f.write("narrow " + " ".join(sys.argv[sys.argv.index("--changed") + 1 :]) + "\\n")
"""


def _with_table(repo: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen"
    (repo / "tools").mkdir(exist_ok=True)
    (repo / "tools" / "affected_tests.py").write_text(READS.format(seen=str(seen)))
    for name in ("read.txt", "CHANGELOG.md"):
        (repo / name).write_text("main's first\n")
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
def _history(repo: Path, tmp_path: Path, judged_adds: str, main_moves: str) -> tuple[str, ...]:
    _with_table(repo, tmp_path)
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
    judged, tip, base = _history(repo, tmp_path, "gates/x.sh", "gates/y.sh")
    code, why, _ = pre_reuse.decide(repo, judged, tip, base)
    assert code == pre_reuse.RERUN and "gates/y.sh moved" in why, why
    assert pre_reuse.decide(repo, judged, "0" * 40, base)[0] == pre_reuse.RERUN
    assert pre_reuse.decide(repo, judged, tip, "0" * 40)[0] == pre_reuse.RERUN
    assert pre_reuse.decide(repo, judged, judged, base)[0] == pre_reuse.REUSE  # the same tree


def test_a_moved_path_the_branch_changes_too_is_run_again(repo, tmp_path):
    """Measured from a base below main's move, the branch's own diff holds the moved file: a
    merged file is in neither judged tree."""
    judged, tip, _ = _history(repo, tmp_path, "lane.txt", "CHANGELOG.md")
    below = git(repo, "rev-parse", f"{judged}~1").strip()
    code, why, _ = pre_reuse.decide(repo, judged, tip, below)
    assert code == pre_reuse.RERUN and "CHANGELOG.md moved on main and is changed by" in why, why
    assert pre_reuse.decide(repo, judged, tip, git(repo, "rev-parse", f"{tip}~1").strip())[0] == 0


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
