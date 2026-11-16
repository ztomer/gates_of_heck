"""tools/affected_tests.py and land.sh's pre-step: the tests a land runs first come from the diff.

GOH.TREETESTSFIRST. GOH.ONEADMISSION's first land (2026-11-16) went red on tests/test_lock_order.py
after a slot wait and the suite: the lane's hand-picked list missed a test that RUNS the changed
tools/load.py by path. GOH.TAGPUSHGATE's went red on tests/test_self_walk.py, a tree reader no name
pattern sees. Here: each way a test can depend on a change is chosen, a test depending on nothing
is not, the readers that cost lands are chosen on an empty diff, and land.sh runs the declared
pre-step before the land lock and the gate, a red one landing nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, git, hermetic_env
from test_land import LAND, _main, _worktree
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

# Tree readers that went unrun by a lane and failed its land (the queue's notes, 2026-11-15/16).
COST_A_LAND = (
    "tests/test_self_walk.py",  # GOH.TAGPUSHGATE land2: no name pattern sees it
    "tests/test_python_from_head.py",  # GOH.SHIPRELREUSE land3
    "tests/test_suite_drift.py",  # GOH.LOADEDDROP land1
    "tests/test_bash5_guard.py",  # GOH.SHIPRELREUSE land1
    "tests/test_no_env_spread.py",  # GOH.SHIPRELREUSE land1
    "tests/test_wiring.py",  # GOH.SHIPRELREUSE land3 (a script's map row)
    "tests/test_wall_clock_ratchet.py",  # GOH.SHIPRELREUSE land3
    "tests/test_config_schema.py",  # ba09c92 (a GOH_ key with no docs/config.md row)
)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_a_test_that_runs_a_changed_file_by_path_is_chosen_on_the_real_tree() -> None:
    """The ONEADMISSION miss: tests/test_lock_order.py runs `REPO_ROOT / "tools" / "load.py"`."""
    why = at.affected(REPO_ROOT, ["tools/load.py"])
    assert why.get("tests/test_lock_order.py") == "names tools/load.py", why.get(
        "tests/test_lock_order.py"
    )


@pytest.mark.parametrize(
    "text",
    [
        'X = REPO_ROOT / "tools" / "load.py"\n',
        'X = os.path.join(ROOT, "tools", "load.py")\n',
        "X = 'tools/load.py'\n",
        "import load\n",
        "from load import main\n",
        "from tools import load\n",
        "from tools.load import (\n    main,\n    other,\n)\n",
    ],
)
def test_every_way_a_test_names_or_imports_a_changed_file_chooses_it(tmp_path, text) -> None:
    root = _tree(tmp_path, {"tests/test_x.py": text, "tools/load.py": ""})
    assert "tests/test_x.py" in at.affected(root, ["tools/load.py"])


def test_a_test_that_names_nothing_changed_and_reads_no_tree_is_not_chosen(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_x.py": 'import landq\nX = REPO_ROOT / "tools" / "ship.py"\n',
            "tests/test_load.py": "def test_y():\n    pass\n",  # its name is not the module's
        },
    )
    assert at.affected(root, ["tools/load.py"]) == {}


def test_a_changed_test_and_a_test_importing_a_changed_helper_are_chosen(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_a.py": "",
            "tests/test_b.py": "from _kit import make\n",
            "tests/_kit.py": "",
        },
    )
    assert at.affected(root, ["tests/test_a.py", "tests/_kit.py"]) == {
        "tests/test_a.py": "changed",
        "tests/test_b.py": "imports _kit (tests/_kit.py)",
    }


@pytest.mark.parametrize(
    "text",
    [
        "def test_every_script_is_listed():\n    pass\n",
        "def test_no_bare_python():\n    pass\n",
        "out = git('ls-files')\n",
        "for p in REPO_ROOT.rglob('*.sh'):\n    pass\n",
        "for d, _, fs in os.walk(REPO_ROOT):\n    pass\n",
        "S = {p.name for p in (REPO_ROOT / 'hooks').iterdir()}\n",
        "S = sorted((REPO_ROOT / 'tools').glob('install_*.sh'))\n",
        "import _walker\n",  # a tests/ helper that walks the tree
    ],
)
def test_every_shape_of_tree_reader_is_chosen_whatever_the_diff(tmp_path, text) -> None:
    root = _tree(
        tmp_path,
        {"tests/test_r.py": text, "tests/_walker.py": "import os\nos.walk('.')\n"},
    )
    assert "tests/test_r.py" in at.affected(root, [])


def test_the_tree_readers_that_cost_a_land_are_chosen_on_an_empty_diff() -> None:
    chosen = at.affected(REPO_ROOT, [])
    assert [t for t in COST_A_LAND if t not in chosen] == []


def test_every_always_entry_is_a_test_file_that_exists() -> None:
    """ALWAYS is a list kept by hand: an entry naming no file is stale and fails here."""
    assert [p for p in at.ALWAYS if not (REPO_ROOT / p).is_file()] == []


def test_a_root_file_is_named_only_joined_to_the_repo_root(tmp_path) -> None:
    """80 fixture tests write their own `.gatesrc`; the ones reading goh's say REPO_ROOT / it."""
    root = _tree(
        tmp_path,
        {
            "tests/test_own.py": 'X = REPO_ROOT / ".gatesrc"\n',
            "tests/test_fixture.py": '(wt / ".gatesrc").write_text("A=1")\n',
        },
    )
    assert list(at.affected(root, [".gatesrc"])) == ["tests/test_own.py"]


def test_a_deleted_file_still_named_by_a_test_chooses_that_test(tmp_path) -> None:
    root = _tree(tmp_path, {"tests/test_x.py": 'X = ROOT / "lib" / "gone.sh"\n'})
    assert "tests/test_x.py" in at.affected(root, ["lib/gone.sh"])


def test_an_empty_set_runs_nothing(tmp_path) -> None:
    """tools/pytest.sh with no file runs the whole suite: the pre-step must never ask for that."""
    root = _tree(tmp_path, {"tests/test_x.py": ""})  # no tools/pytest.sh to run
    assert at.run(root, ["README.md"], {}) == 0


def test_inside_a_running_suite_the_set_is_named_not_run(tmp_path, monkeypatch, capsys) -> None:
    """A fixture land of a goh copy runs inside the suite: pytest.sh would refuse the nesting."""
    monkeypatch.setenv("GATES_OF_HECK_SUITE_RUNNER", "1")
    monkeypatch.delenv("GATES_OF_HECK_SUITE_NESTED_OK", raising=False)
    root = _tree(tmp_path, {"tests/test_x.py": ""})  # no tools/pytest.sh: a run would exit 127
    assert at.run(root, [], {"tests/test_x.py": "changed"}) == 0
    assert "not run: inside a running goh suite" in capsys.readouterr().out


def _declare(wt: Path, body: str) -> None:
    write_gatesrc(wt, body)
    git(wt, "add", ".gatesrc")
    git(wt, "commit", "-q", "--no-verify", "-m", "declare")


def _land(wt: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True)
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          timeout=patience(60), stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_red_pre_step_lands_nothing_and_the_gate_never_runs(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(
        wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='echo pre >> {seen}; exit 3'\n"
    )
    before = _main(repo)
    r = _land(wt)
    assert r.returncode == 1 and "pre-land tests failed" in r.stderr, r.stdout + r.stderr
    assert seen.read_text() == "pre\n" and _main(repo) == before


def test_the_pre_step_runs_before_the_land_lock_with_mains_tip(repo: Path, tmp_path: Path) -> None:
    """Before the lock: a land lock file nobody has written yet (land.sh writes its holder line
    on taking it). $1 is main's tip, which the selector diffs against."""
    wt = _worktree(repo, tmp_path)
    seen, lock = tmp_path / "seen", repo / ".git" / "goh-land.lock"
    _declare(
        wt,
        f"GOH_LAND_GATE='echo gate >> {seen}'\n"
        f"GOH_LAND_PRE='echo \"pre $1 $(cat {lock} 2>/dev/null)\" >> {seen}'\n",
    )
    base, tip = _main(repo), git(wt, "rev-parse", "HEAD").strip()
    r = _land(wt)
    assert r.returncode == 0, r.stdout + r.stderr
    assert seen.read_text() == f"pre {base} \ngate\n" and _main(repo) == tip
    assert "pre-land green in" in r.stdout, r.stdout


def test_a_repo_declaring_no_pre_step_lands_as_before(repo: Path, tmp_path: Path) -> None:
    wt = _worktree(repo, tmp_path)
    _declare(wt, "GOH_LAND_GATE=true\n")
    r = _land(wt)
    assert r.returncode == 0 and "pre-land" not in r.stdout, r.stdout + r.stderr


def test_goh_declares_the_selector_as_its_pre_step() -> None:
    """goh's .gatesrc names the selector, read the way land.sh reads it."""
    pre = subprocess.run(
        ["bash", "-c", '. ./.gatesrc >/dev/null 2>&1 </dev/null; printf %s "${GOH_LAND_PRE-}"'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        env=hermetic_env(drop_git=True),
    ).stdout  # fmt: skip
    assert 'tools/affected_tests.py --run --base "$1"' in pre, pre
