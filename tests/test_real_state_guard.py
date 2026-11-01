"""The suite's backstop against a test writing the machine's real goh state (real_state_guard.py)."""

import os
import tempfile

import real_state_guard as g


def test_a_scratch_line_added_to_the_real_registry_is_named(tmp_path):
    state = tmp_path / "goh"
    state.mkdir()
    reg = state / "installed_repos"
    reg.write_text("/Users/someone/Projects/live\n")
    before = g.snapshot(state)
    scratch = os.path.join(os.path.realpath(tempfile.gettempdir()), "tmp.X1/boxa")
    reg.write_text(f"/Users/someone/Projects/live\n/Users/someone/Projects/new\n{scratch}\n")
    bad = g.scratch_lines_added(before, g.snapshot(state), g.scratch_roots("/Users/someone"))
    assert bad == [f"installed_repos: {scratch}"], bad


def test_a_live_repo_installed_meanwhile_is_not_a_test_writing_state(tmp_path):
    state = tmp_path / "goh"
    state.mkdir()
    before = g.snapshot(state)
    (state / "installed_repos").write_text("/Users/someone/Projects/live\n")
    assert g.scratch_lines_added(before, g.snapshot(state), g.scratch_roots("/Users/x")) == []


def test_the_suite_writes_state_only_into_its_own_home():
    """The isolation the guard backs: the suite's HOME and XDG_STATE_HOME are not the user's."""
    real = os.environ["GATES_OF_HECK_SUITE_REAL_HOME"]
    assert os.path.realpath(os.path.expanduser("~")) != real
    assert not os.path.realpath(os.environ["XDG_STATE_HOME"]).startswith(real + "/.local/")
