"""A change to the goh binary's source chooses every test that runs the binary (GOH.AFFECTEDBINARY).

GRACESHAPES (2026-11-17) changed crates/goh scanners; the pre-step (tools/affected_tests.py --run)
was green and the full land gate went red on tests/test_check_moving_tags.py, which runs
`goh moving-tags` and pinned its output. The test names no crates/ path and imports no changed
module, so the diff never chose it: the edge source -> binary -> every test that invokes it was
missing. Pinned here: each way a test runs the binary is found (the `goh`/`native_goh` fixture,
`run_goh`/`native_goh_path`, the env keys and `bin/goh` by path, through a helper, through a script
that runs it), prose that merely mentions one is not, a build input changes them all and nothing
else does, the real tree's list contains the incident, and the verdict carry (GOH.VERDICTCARRY)
reruns each across a build change whether or not its run was marked.
"""

from __future__ import annotations

import ast
import re
import sys

import _land_verdicts as lv
import _tree_graph
import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from checks import _prose_mask  # the root is on sys.path now (affected_tests put it there)

SCANNER = "crates/goh/src/layerparity.rs"
INCIDENT = "tests/test_check_moving_tags.py"  # GRACESHAPES, 2026-11-17: red in the land gate


def _tree(tmp_path, files: dict[str, str]):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


def test_the_scanner_change_that_went_red_in_a_land_gate_chooses_the_test_that_runs_it() -> None:
    why = at.affected(REPO_ROOT, [SCANNER], graph=_tree_graph.real())
    assert INCIDENT in why, sorted(why)
    assert "goh binary" in why[INCIDENT] and SCANNER in why[INCIDENT], why[INCIDENT]


RUNNERS = {
    "tests/test_fixture.py": "def test_a(goh, tmp_path):\n    pass\n",
    "tests/test_native.py": "def test_a(native_goh):\n    pass\n",
    "tests/test_run.py": "from conftest import run_goh\n\ndef test_a():\n    run_goh('.', 'x')\n",
    "tests/test_path.py": "from conftest import native_goh_path\nP = native_goh_path\n",
    "tests/test_env.py": "import os\nB = os.environ['GOH_TEST_BIN']\n",
    "tests/test_bin.py": "P = ROOT / 'bin' / 'goh'\nQ = 'bin/goh'\n",
    "tests/test_helper.py": "import _kit\n",
    "tests/_kit.py": "def go():\n    return run_goh('.')\n",
    "tests/test_script.py": "S = ROOT / 'gates' / 'x.sh'\n",
    "gates/x.sh": 'source "$GOH_DIR/gates/_y.sh"\n',
    "gates/_y.sh": '"$GOH_RESOLVED_BIN" step true\n',
}
PURE = {
    "tests/test_pure.py": "def test_a(tmp_path):\n    assert 1\n",
    "tests/test_prose.py": '"""Runs bin/goh and run_goh.\n"""\n# native_goh, GOH_BIN\n',
    "tests/test_other_arg.py": "def test_a(goh_like, hogh):\n    pass\n",
}
BUILD_INPUTS = [
    "crates/goh/src/main.rs",
    "crates/goh-tui/Cargo.toml",
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
]


@pytest.mark.parametrize("path", BUILD_INPUTS)
def test_a_build_input_chooses_each_test_that_runs_the_binary_and_no_other(tmp_path, path) -> None:
    root = _tree(tmp_path, {**RUNNERS, **PURE})
    chosen = {t for t in at.affected(root, [path]) if "/test_" in t}
    pure = {t for t in chosen if t in PURE}
    runners = {t for t in RUNNERS if "/test_" in t}
    # tests/test_helper.py reaches the helper that runs it; the script test reaches the gate script
    assert chosen == runners and not pure, (sorted(chosen ^ runners), sorted(pure))


@pytest.mark.parametrize("path", ["lib/x.py", "docs/crates.md", "crates_notes.md", "tests/x.txt"])
def test_a_path_that_is_no_build_input_chooses_none_of_them(tmp_path, path) -> None:
    root = _tree(tmp_path, {**RUNNERS, **PURE, "lib/x.py": ""})
    assert at.affected(root, [path]) == {}


def test_every_test_that_asks_the_binary_by_a_plain_read_is_chosen_on_the_real_tree() -> None:
    """The class, found a second way: the fixture or helper names a test asks for, read raw. The
    selector's own reading (code only, through helpers) may be wider, never narrower. A change to
    main.rs reaches every subcommand; a scanner's chooses only its subcommands' runners
    (GOH.AFFECTEDBINARY2, tests/test_affected_binary_reach.py)."""
    asked = re.compile(r"\b(?:run_goh|native_goh_path|native_goh|GOH_TEST_BIN)\b")
    chosen = at.affected(REPO_ROOT, ["crates/goh/src/main.rs"], graph=_tree_graph.real())
    missed = []
    for p in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text)
        args = {
            a.arg for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) for a in n.args.args
        }
        code = _prose_mask.python_code_only(text)
        if (asked.search(code) or args & {"goh", "native_goh"}) and f"tests/{p.name}" not in chosen:
            missed.append(p.name)
    assert not missed, missed


# -- the verdict carry: a passed binary test is re-run across a build change -----------------------


@pytest.mark.parametrize("marked", [True, False])
def test_the_carry_reruns_a_binary_test_it_had_passed_across_a_planted_crates_change(
    marked,
) -> None:
    """GOH.VERDICTCARRY says no binary test is carried across a build change. The mark
    (`tests/_goh_request.py`) only sees a test that asks for `native_goh`; a test that runs the
    binary through a gate script or an env key records `False`, and the carry skipped it. The plan
    is the one tests/_land_verdicts.py `_carry` makes: affected_tests' choice for the diff, then
    `carried` with whether the diff changes the build."""
    changed = ["crates/goh/src/layerparity.rs"]
    chosen, why = lv.choose(REPO_ROOT, changed)
    assert chosen is not None, why
    passed = {
        f"{INCIDENT}::test_the_incident_is_named_at_each_line_with_the_fix": marked,
        "tests/test_gates_e2e.py::test_any": marked,
        "tests/test_no_such_pure_neighbour.py::test_any": False,
    }
    carried = lv.carried(passed, chosen, coverage=False, binary=lv._builds(changed))
    assert carried == {"tests/test_no_such_pure_neighbour.py::test_any"}, carried
