"""tools/affected_tests.py and land.sh's pre-step: the tests a land runs first come from the diff.

GOH.TREETESTSFIRST. GOH.ONEADMISSION's first land (2026-11-16) went red on tests/test_lock_order.py
after a slot wait and the suite: the lane's hand-picked list missed a test that RUNS the changed
tools/load.py by path. GOH.TAGPUSHGATE's went red on tests/test_self_walk.py, a tree reader no name
pattern sees. Here: each way a test can depend on a change is chosen, a test depending on nothing
is not, the readers that cost lands are chosen on an empty diff, and land.sh runs the declared
pre-step beside the gate under the land lock (GOH.GATEPARALLEL), a red one landing nothing.
"""

from __future__ import annotations

import json
import os
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


def test_the_gate_slot_race_test_is_chosen_for_the_wait_text_its_probe_reads() -> None:
    """GOH.AFFECTEDTRANSITIVE: SLOTFAIR land 1 lost 706 s. tests/test_gate_slot_race.py imports
    checks/_gate_slot_probe.py, whose instrumented copy reads lib/gate_slot.py's wait text; it names
    that path nowhere itself, and the one-level selection stopped at the test."""
    why = at.affected(REPO_ROOT, ["lib/gate_slot.py"])
    assert "tests/test_gate_slot_race.py" in why, sorted(why)


def test_a_helper_a_test_imports_that_names_a_changed_path_chooses_the_test(tmp_path) -> None:
    """The probe shape: the test imports a checks/ module that reads (names) the changed file."""
    root = _tree(
        tmp_path,
        {
            "tests/test_race.py": "from _probe import Trial\n",
            "checks/_probe.py": 'WAIT = "lib/waiter.py"\n',
            "lib/waiter.py": "",
        },
    )
    assert at.affected(root, ["lib/waiter.py"]) == {
        "tests/test_race.py": "reaches lib/waiter.py through checks/_probe.py -> lib/waiter.py"
    }


def test_a_helper_naming_a_data_file_chooses_the_tests_that_reach_it(tmp_path) -> None:
    """A data file is no script, so no edge: the helper in the closure NAMES it."""
    root = _tree(
        tmp_path,
        {
            "tests/test_race.py": "from _probe import Trial\n",
            "checks/_probe.py": 'WAIT = "docs/wait.txt"\n',
            "tests/test_other.py": "",
        },
    )
    assert at.affected(root, ["docs/wait.txt"]) == {
        "tests/test_race.py": "reaches checks/_probe.py, which names docs/wait.txt"
    }


def test_what_pytest_loads_before_every_test_does_not_choose_every_test(tmp_path) -> None:
    """tests/conftest.py's closure is in every test's: followed, a change in it chose 443 of 464
    files. It is the base; the diff chooses by what a test itself imports or names."""
    root = _tree(
        tmp_path,
        {
            "tests/conftest.py": "import shared\n",
            "lib/shared.py": "",
            "tests/test_a.py": "from conftest import X\n",
            "tests/test_b.py": "from conftest import X\nimport shared\n",
        },
    )
    assert list(at.affected(root, ["lib/shared.py"])) == ["tests/test_b.py"]


def test_prose_naming_a_changed_path_is_not_a_read_of_it(tmp_path) -> None:
    """A docstring or comment in a module the test imports mentions the file; code names it."""
    root = _tree(
        tmp_path,
        {
            "tests/test_a.py": "import _prose\n",
            "tests/test_b.py": "import _code\n",
            "tests/_prose.py": '"""Reads docs/wait.txt."""\n# and docs/wait.txt\n',
            "tests/_code.py": 'P = ROOT / "docs" / "wait.txt"\n',
        },
    )
    assert list(at.affected(root, ["docs/wait.txt"])) == ["tests/test_b.py"]


def test_a_script_a_test_runs_does_not_bring_the_scripts_it_mentions(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_run.py": 'S = REPO_ROOT / "tools" / "run.sh"\n',
            "tools/run.sh": '#!/bin/bash\nexec "$GOH_DIR/tools/inner.sh" docs/data.txt\n',
            "tools/inner.sh": '#!/bin/bash\n. "$GOH_DIR/lib/deep.sh"\n',
            "lib/deep.sh": "",
        },
    )
    # run.sh EXECS inner.sh by a rooted path, so what inner.sh sources is in the test's closure
    # (GOH.PREPARITY: this line pinned {} -- the blindness that missed test_rust_gate.py)
    assert list(at.affected(root, ["lib/deep.sh"])) == ["tests/test_run.py"]
    assert at.affected(root, ["docs/data.txt"]) == {}  # the script's own mention is not a run


def test_a_script_a_sourced_gate_runs_by_a_rooted_path_chooses_the_test(tmp_path) -> None:
    """GOH.BUILDINCLUDES 73ba690b: tests/test_rust_gate.py runs gates/rust_gate.sh, which sources
    gates/_rust_proven.sh, which runs `"$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py"`. The
    pre-step did not choose the test for a lib/gate_identity.py change; the land gate went red on
    14 of its tests. A path rooted at a variable in a shell script's code is a use of the file,
    direct or through a variable that holds it; a bare path in a message is not."""
    root = _tree(
        tmp_path,
        {
            "tests/test_gate.py": 'G = REPO_ROOT / "gates" / "fx_gate.sh"\n',
            "gates/fx_gate.sh": '#!/bin/bash\n. "$HERE/_fx_proven.sh"\n',
            "gates/_fx_proven.sh": (
                '#!/bin/bash\nid="$("$GOH_PY" -S "$ROOT/lib/ident.py" rust)"\n'
                '_PY="$ROOT/lib/keyed.py"\n"$GOH_PY" "$_PY" keyed\n'
                'err "see lib/told.py for why"  # a message names it: no run\n'
            ),
            "lib/ident.py": "",
            "lib/keyed.py": "",
            "lib/told.py": "",
        },
    )
    assert list(at.affected(root, ["lib/ident.py"])) == ["tests/test_gate.py"]
    assert list(at.affected(root, ["lib/keyed.py"])) == ["tests/test_gate.py"]
    assert at.affected(root, ["lib/told.py"]) == {}


def test_the_rust_gate_test_is_chosen_for_a_gate_identity_change_on_the_real_tree() -> None:
    why = at.affected(REPO_ROOT, ["lib/gate_identity.py"])
    assert "tests/test_rust_gate.py" in why, sorted(k for k in why if "rust_gate" in k)


def test_a_fixture_chain_of_three_imports_is_followed(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_a.py": "from _b import x\n",
            "tests/_b.py": "from _c import x\n",
            "tests/_c.py": "import leaf\n",
            "lib/leaf.py": "",
            "tests/test_other.py": "import _unrelated\n",
            "tests/_unrelated.py": "",
        },
    )
    assert at.affected(root, ["lib/leaf.py"]) == {
        "tests/test_a.py": "reaches lib/leaf.py through tests/_b.py -> tests/_c.py -> lib/leaf.py"
    }


def test_a_test_running_a_script_by_path_inherits_what_the_script_sources(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_run.py": 'S = REPO_ROOT / "tools" / "run.sh"\n',
            "tools/run.sh": '#!/bin/bash\n. "$GOH_DIR/lib/inner.sh"\n',
            "lib/inner.sh": "",
            "tests/test_other.py": 'S = REPO_ROOT / "tools" / "other.sh"\n',
            "tools/other.sh": "#!/bin/bash\n",
        },
    )
    assert list(at.affected(root, ["lib/inner.sh"])) == ["tests/test_run.py"]


def test_an_import_cycle_ends_and_a_missing_module_is_no_edge(tmp_path) -> None:
    root = _tree(
        tmp_path,
        {
            "tests/test_a.py": "import _b\nimport no_such_module\n",
            "tests/_b.py": "import _c\n",
            "tests/_c.py": "import _b\nimport leaf\n",
            "lib/leaf.py": "",
        },
    )
    assert list(at.affected(root, ["lib/leaf.py"])) == ["tests/test_a.py"]


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
    assert "tests/test_r.py" in _files(at.affected(root, []))


def _files(chosen: dict[str, str]) -> set[str]:
    """The files a set runs, whole or by their reading tests (GOH.LANDPREDUP)."""
    return {k.split("::")[0] for k in chosen}


def test_the_tree_readers_that_cost_a_land_are_chosen_on_an_empty_diff() -> None:
    chosen = _files(at.affected(REPO_ROOT, []))
    assert [t for t in COST_A_LAND if t not in chosen] == []


def test_every_always_entry_is_a_test_file_that_exists() -> None:
    """ALWAYS is a list kept by hand: an entry naming no file is stale and fails here."""
    assert [p for p in at.ALWAYS if not (REPO_ROOT / p).is_file()] == []


def test_every_text_judge_is_a_tree_reader_that_exists() -> None:
    """TEXT_JUDGES is kept by hand too (GOH.REUSEOFFLANDED5): each names a file, and a tree reader,
    so the land runs it on every change as the release gate runs it on the release text."""
    readers = at.tree_readers(REPO_ROOT)
    assert [p for p in at.TEXT_JUDGES if p not in readers] == []


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


def test_a_pre_step_that_runs_no_test_says_so_in_its_record(tmp_path, monkeypatch) -> None:
    """tools/land.sh refuses a green pre-step that wrote no record in a tree whose suite records
    (GOH.VERDICTCARRY2): when this runs no test, the record it hands back names why, and holds no
    verdict; /dev/null (a run by hand) is never written."""
    root = _tree(tmp_path, {"tests/test_x.py": ""})
    rec = tmp_path / "rec.json"
    monkeypatch.setenv("GOH_LAND_VERDICTS_RECORD", str(rec))
    assert at.run(root, ["README.md"], {}) == 0
    got = json.loads(rec.read_text())
    assert got["passed"] == {} and got["identity"] is None and "no test is chosen" in got["ran"]
    rec.unlink()
    monkeypatch.setenv("GATES_OF_HECK_SUITE_RUNNER", "1")
    monkeypatch.delenv("GATES_OF_HECK_SUITE_NESTED_OK", raising=False)
    assert at.run(root, [], {"tests/test_x.py": "changed"}) == 0
    assert "inside a running goh suite" in json.loads(rec.read_text())["ran"]
    monkeypatch.setenv("GOH_LAND_VERDICTS_RECORD", os.devnull)
    assert at.run(root, [], {"tests/test_x.py": "changed"}) == 0


def _declare(wt: Path, body: str) -> None:
    write_gatesrc(wt, body)
    git(wt, "add", ".gatesrc")
    git(wt, "commit", "-q", "--no-verify", "-m", "declare")


def _land(wt: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(drop_git=True, **extra)
    env.pop("GATES_OF_HECK_SUITE_RUNNER", None)
    return subprocess.run(["bash", str(LAND)], cwd=wt, env=env, capture_output=True, text=True,
                          timeout=patience(60), stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_a_red_pre_step_lands_nothing(repo: Path, tmp_path: Path) -> None:
    """The gate runs beside it (GOH.GATEPARALLEL) and may have finished first: nothing lands."""
    wt = _worktree(repo, tmp_path)
    seen = tmp_path / "seen"
    _declare(
        wt, f"GOH_LAND_GATE='echo gate >> {seen}'\nGOH_LAND_PRE='echo pre >> {seen}; exit 3'\n"
    )
    before = _main(repo)
    r = _land(wt)
    assert r.returncode == 1 and "pre-land tests failed" in r.stderr, r.stdout + r.stderr
    assert "pre\n" in seen.read_text() and _main(repo) == before


def test_the_pre_step_runs_under_the_land_lock_beside_the_gate_with_mains_tip(
    repo: Path, tmp_path: Path
) -> None:
    """Under the lock, beside the gate (GOH.GATEPARALLEL): the lock file holds this land's holder
    line (land.sh writes it on taking the lock). $1 is main's tip, which the selector diffs
    against. The enqueue's run (GOH_LAND_PRE_ONLY) is the one before any lock."""
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
    lines = sorted(seen.read_text().splitlines())
    assert lines[0] == "gate" and lines[1].startswith(f"pre {base} feature @ "), lines
    assert _main(repo) == tip
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
    assert 'tools/affected_tests.py --run --structural --base "$1"' in pre, pre
