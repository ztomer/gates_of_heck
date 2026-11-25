"""The goh binary's build inputs are DERIVED, and every reader asks (GOH.BUILDINCLUDES).

lib/gate_identity.py BUILD named the four roots (crates/ Cargo.toml Cargo.lock rust-toolchain.toml).
The binary also embeds files outside crates/ (`include_str!("../../../lib/busyloop_shapes.json")`),
and the affected-tests edge (a build change chooses every test that runs the binary) and the
verdict carry (no binary test carried across a build change) read the four roots: an edit to the
busy-loop shapes alone chose no binary test, and the carry kept a stale green. Both now ask
`gate_identity.build_inputs` (lib/goh_version.sh --inputs, from the crates' own include literals).
Pinned here: a planted include outside crates/ joins the set and chooses and re-runs the binary's
tests, the real tree's embeds choose them, no module but gate_identity names the root tuple, and
each build input is keyed by the gate identity's content (a new include outside its parts fails
until covered).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import _land_verdicts as lv
import _tree_graph
import pytest
from conftest import REPO_ROOT, commit_all, write

sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "lib"))
import affected_tests as at
import gate_identity

EMBED = "lib/busyloop_shapes.json"  # embedded by crates/goh/src/busyloop.rs, outside crates/
RUNNER = "tests/test_runs_goh.py"
PLANTED = "data/planted.json"


def _planted(repo: Path) -> Path:
    """A committed tree (the `repo` fixture): a crate that includes PLANTED (outside crates/), one
    test that runs the binary, one that does not."""
    files = {
        "Cargo.toml": "[workspace]\n",
        "crates/app/src/main.rs": f'const P: &str = include_str!("../../../{PLANTED}");\n',
        PLANTED: "{}\n",
        RUNNER: "def test_a(goh):\n    pass\n",
        "tests/test_pure.py": "def test_a():\n    assert 1\n",
    }
    for rel, text in files.items():
        write(repo, rel, text)
    commit_all(repo)
    return repo


def test_a_planted_include_outside_crates_joins_the_build_inputs(repo: Path) -> None:
    root = _planted(repo)
    inputs = gate_identity.build_inputs(root)
    assert PLANTED in inputs and inputs[:4] == list(gate_identity.BUILD), inputs


def test_a_planted_include_chooses_each_test_that_runs_the_binary_and_no_other(
    repo: Path,
) -> None:
    chosen = at.affected(_planted(repo), [PLANTED])
    assert RUNNER in chosen and "tests/test_pure.py" not in chosen, sorted(chosen)


def test_the_real_embeds_choose_the_tests_that_run_the_binary() -> None:
    """`tools/affected_tests.py --changed lib/busyloop_shapes.json`, which the four roots missed."""
    chosen = _tree_graph.affected([EMBED])
    assert "tests/test_check_moving_tags.py" in chosen, sorted(chosen)
    assert "goh binary" in chosen["tests/test_check_moving_tags.py"], chosen


def test_the_carry_reruns_a_binary_test_across_a_planted_include_change(repo: Path) -> None:
    root = _planted(repo)
    changed = [PLANTED]
    chosen, why = lv.choose(root, changed)
    assert chosen is not None, why
    passed = {f"{RUNNER}::test_a": False, "tests/test_pure.py::test_a": False}
    binary = lv._builds(changed, root)
    assert binary, "a planted include is no build change to the carry"
    assert lv.carried(passed, chosen, coverage=False, binary=binary) == {
        "tests/test_pure.py::test_a"
    }


@pytest.mark.parametrize("path", [EMBED, "gates/required_tools.tsv", "tui/stylerc"])
def test_the_carry_sees_each_real_embed_as_a_build_change(path: str) -> None:
    assert lv._builds([path]) and not lv._builds(["lib/reuse.py", "docs/map.md"])


def test_no_module_but_gate_identity_reads_the_root_tuple() -> None:
    """BUILD is the roots; a reader of it alone is the stale four-path answer (the class)."""
    readers = []
    for d in ("lib", "tools", "tests", "checks", "gates"):
        for p in sorted((REPO_ROOT / d).rglob("*.py")):
            if p.name != "test_build_includes.py" and re.search(
                r"\bgate_identity\.BUILD\b|\bgi\.BUILD\b|_lib\(\"gate_identity\"\)\.BUILD",
                p.read_text(encoding="utf-8"),
            ):
                readers.append(p.relative_to(REPO_ROOT).as_posix())
    assert not readers, f"read the four roots, not gate_identity.build_inputs: {readers}"


def test_every_build_input_is_keyed_by_the_gate_identity_content() -> None:
    """An include of a file outside PARTS would change the binary and not the identity."""
    unkeyed = [
        p for p in gate_identity.build_inputs(REPO_ROOT)
        if not any(p == k or p.startswith(f"{k}/") for k in gate_identity.PARTS)
    ]  # fmt: skip
    assert not unkeyed, f"embedded by a crate, outside gate_identity.PARTS: {unkeyed}"
