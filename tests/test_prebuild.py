"""The controller resolves the goh every gate a test spawns will ask for (tests/_prebuild.py).

G2c: a checkout whose bin/goh is not HEAD's build (a fresh worktree, a commit whose crates moved)
made the FIRST gate a test spawned build goh -- and the drift guard refuses a build behind the
session's back, so every gate step that runs goh failed (9 of test_rust_gate.py, in any new
worktree). GOH.PYHARNESS runs the resolve beside the run and joins it before the first test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import _drift_guard
import _prebuild
import pytest
from conftest import REPO_ROOT
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import pid_owner


class _Config:
    """The controller's: no workerinput, the options and the registry the prebuild asks."""

    def __init__(self, **options) -> None:
        self.options, self.plugins = options, []
        self.pluginmanager = self

    def getoption(self, name: str, default=None):
        return self.options.get(name, default)

    def register(self, plugin, name: str) -> None:
        self.plugins.append(plugin)


def _resolver(root: Path, body: str) -> Path:
    (root / "gates").mkdir()
    (root / "gates" / "_goh_bin.sh").write_text(
        # No Rust delta (goh_live_binary does not apply), and bin/goh is not HEAD's build.
        f"goh_live_binary() {{ return 1; }}\ngoh_resolve_native() {{ {body}; }}\n"
    )
    return root


@pytest.fixture
def controller(monkeypatch):
    # The controller's own environment, stated: a suite under GOH_LIVE and not a commit's source
    # guard. Inherited, the land check's guard run (GOH_STAGED_GUARD_RUN, or no GOH_LIVE) made
    # prebuild_live return before resolving, and this red there while green in a plain run.
    monkeypatch.setenv("GOH_LIVE", "1")
    monkeypatch.delenv("GOH_STAGED_GUARD_RUN", raising=False)


def test_the_controller_resolves_the_binary_every_gate_will_ask_for(tmp_path, controller):
    """The working tree's build under GOH_LIVE with a Rust delta, otherwise HEAD's."""
    called = tmp_path / "resolved"
    _resolver(tmp_path, f': > "{called}"; goh_native=/bin/true; goh_native_why=""')
    config = _Config()
    build = _drift_guard.prebuild_live(config, root=tmp_path)
    assert config.plugins == [build], "nothing would join it"
    assert build.pytest_runtestloop(session=None) is None, "an answer skips every test"
    assert called.exists(), "the controller left the first gate to build goh behind the session"


def test_a_build_that_fails_is_the_runs_usage_error_in_its_own_words(tmp_path, controller):
    _resolver(tmp_path, 'goh_native=""; goh_native_why="no cargo here"')
    build = _prebuild.prebuild_live(_Config(), root=tmp_path)
    with pytest.raises(pytest.UsageError, match="did not build:\nno cargo here"):
        build.pytest_runtestloop(session=None)


@pytest.mark.load_sensitive
def test_a_run_that_never_reaches_its_tests_stops_the_build_and_its_children(tmp_path, controller):
    mark, fifo = tmp_path / "pid", tmp_path / "never-written"
    os.mkfifo(fifo)
    _resolver(
        tmp_path, f'cat "{fifo}" & echo "$!" > "{mark}.part"; mv "{mark}.part" "{mark}"; wait'
    )
    build = _prebuild.prebuild_live(_Config(), root=tmp_path)
    give_up = GiveUp(20)
    while not mark.exists():
        assert not give_up.passed(), "the resolver never started"
    child = pid_owner.stamp_line(int(mark.read_text()))
    assert child, "the resolver's child ended by itself: nothing to stop"
    build.pytest_unconfigure(config=None)
    assert build.proc.returncode == -9, build.proc.returncode
    while pid_owner.alive_line(child):
        assert not give_up.passed(), f"{child} outlived the run"


@pytest.mark.parametrize("case", ["worker", "collect-only", "guard", "head"])
def test_a_process_that_spawns_no_gate_starts_none(tmp_path, controller, monkeypatch, case):
    config = _Config(collectonly=case == "collect-only")
    if case == "worker":
        config.workerinput = {}
    if case == "guard":
        monkeypatch.setenv("GOH_STAGED_GUARD_RUN", "1")
    if case == "head":
        monkeypatch.delenv("GOH_LIVE")
    assert _prebuild.prebuild_live(config, root=tmp_path) is None
    assert config.plugins == []


@pytest.mark.parametrize("workers", ["0", "2"])
def test_no_test_runs_before_the_build_is_there(tmp_path, workers):
    """The real pytest, serial and under xdist: the one test reads what the build left."""
    built = tmp_path / "built"
    _resolver(tmp_path, f'sleep 1; : > "{built}"; goh_native=/bin/true; goh_native_why=""')
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "conftest.py").write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
        "from pathlib import Path\n"
        "import _prebuild\n"
        "def pytest_configure(config):\n"
        f"    _prebuild.prebuild_live(config, root=Path({str(tmp_path)!r}))\n"
    )
    (suite / "test_it.py").write_text(
        f"from pathlib import Path\ndef test_it():\n    assert Path({str(built)!r}).exists()\n"
    )
    env = dict(os.environ, GOH_LIVE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    env.pop("PYTEST_PLUGINS", None)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "xdist.plugin", "-p", "no:cacheprovider",
         "-n", workers, str(suite)],
        cwd=suite, env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=patience(60), check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout + done.stderr
