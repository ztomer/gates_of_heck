"""A land whose main moved only the Rust build inputs runs structural and the binary readers.

GOH.PRECODERUST.

Cargo.lock and crates/ were counted as the pre-step's own code, so a move there ran the whole
pre-land again (737 + 1122 + 854 s in three lands of one day). Same stand-in table and history
helpers as tests/test_pre_reuse.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, git
from test_landq import env  # noqa: F401  -- the queue's fixture
from test_pre_reuse import _history, _land

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pre_reuse


# A move to the binary's build inputs only (GOH.PRECODERUST): the structural gates run the binary,
# so these were the pre-step's own code and ran the whole set again -- 737 + 1122 + 854 s in three
# lands of one day, with ~50 min of queue wait behind them. What they judge is the binary's own
# behaviour: structural --full and the tests that run the binary, never the rest.
@pytest.mark.parametrize("path", ["Cargo.lock", "crates/goh/src/x.rs"])
def test_a_move_to_the_binarys_build_inputs_runs_its_binary_readers_only(repo, tmp_path, path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", path, kit=True)
    code, why, nodes = pre_reuse.decide(repo, judged, tip, base)
    assert (code, nodes) == (pre_reuse.NARROW, ["tests/test_bin.py"]), why


def test_a_build_input_moving_with_the_runners_code_still_runs_the_whole_set(repo, tmp_path):
    judged, tip, base = _history(repo, tmp_path, "lane.txt", "tools/pytest.sh", kit=True)
    (repo / "Cargo.lock").write_text("the lane's lock\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "lock")
    tip = git(repo, "rev-parse", "HEAD").strip()
    code, why, _ = pre_reuse.decide(repo, judged, tip, base)
    assert code == pre_reuse.RERUN and "tools/pytest.sh moved: the pre-step's own code" in why, why


def test_a_land_after_a_build_input_move_runs_structural_and_the_binary_readers(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "Cargo.lock")
    assert seen == ["full", "narrow +structural Cargo.lock"], log


def test_a_land_after_another_narrow_move_adds_no_structural_run(
    repo: Path,
    tmp_path: Path,
    env: dict,  # noqa: F811 -- the fixture imported above
) -> None:
    seen, log = _land(repo, tmp_path, env, "read.txt")
    assert seen == ["full", "narrow read.txt"], log


def test_the_real_build_inputs_are_not_the_runners_code_and_their_readers_run_the_binary():
    """goh's own tree: Cargo.lock and a crate source are the binary's build, not the code that
    runs the set; the tests they choose are the ones that run the binary."""
    table = pre_reuse._table(REPO_ROOT)
    moved = ["Cargo.lock", "crates/goh/src/length.rs"]
    graph = table.Closure(REPO_ROOT)
    assert pre_reuse.pre_step_code(REPO_ROOT, table, moved, graph, binary=False) == []
    assert pre_reuse.pre_step_code(REPO_ROOT, table, moved, graph) == moved
    assert "tests/test_check_moving_tags.py" in table.readers(REPO_ROOT, ["Cargo.lock"])
