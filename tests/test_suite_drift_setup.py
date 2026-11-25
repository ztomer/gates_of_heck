"""The drift guard's setup ceiling (GOH.SETUPCEILING): a test's setup phase is judged like its call.

The planted-suite helpers are tests/_drift_planted.py's; the call ceiling's own tests are in
tests/test_suite_drift.py.
"""

from __future__ import annotations

import re

import _drift_guard
from _drift_planted import planted, planted_load, took_over_ceiling
from conftest import REPO_ROOT

TREE_READS = ("tests/",)  # the files SETUP_SLOW names

FIXTURE_SLEEPER = (
    "import pytest, time\n\n"
    "@pytest.fixture(scope='module')\n"
    "def slow_fixture():\n    time.sleep(0.6)\n\n"
    "def test_a_first(slow_fixture):\n    pass\n\n"
    "def test_b_second(slow_fixture):\n    pass\n"
)


def test_a_fixture_over_the_setup_ceiling_fails_the_test_that_triggered_it(tmp_path) -> None:
    """GOH.SETUPCEILING: only a test's CALL was judged, so test_self_walk's module fixture spent
    45.5 s on two `structural --full` walks unseen. A module fixture is charged once, to the first
    test that needs it; the test after it finds it built and is not."""
    r = planted(tmp_path, FIXTURE_SLEEPER, setup_ceiling=0.3)
    assert r.returncode == 1, r.stdout + r.stderr
    assert re.search(r"1 passed.*1 error", r.stdout), r.stdout  # the second one cost no setup
    took = took_over_ceiling(r.stdout, "test_planted.py::test_a_first setup", 0.3)
    assert took is not None, r.stdout
    assert took >= 0.6, r.stdout  # the fixture slept 0.6 s; coverage tracing only adds to it
    assert "test_b_second setup" not in r.stdout, r.stdout


def test_a_fixtures_counted_work_makes_room_for_itself_alone(tmp_path) -> None:
    """GOH.TEMPLEAKSETUP: a cold tree lint read 30.5 s against 20 s at load 30. A fixture says how
    much it had to do; one that did none, or the next test's, keeps the plain ceiling."""
    said = "import _setup_work\n" + FIXTURE_SLEEPER.replace(
        "    time.sleep(0.6)\n", "    time.sleep(0.6)\n    _setup_work.did({n}, 100)\n"
    )
    r = planted(tmp_path, said.format(n=3), setup_ceiling=0.3)
    assert r.returncode == 0, r.stdout + r.stderr
    r = planted(tmp_path, said.format(n=0), setup_ceiling=0.3)
    assert r.returncode == 1, r.stdout + r.stderr
    next_one = said.format(n=3) + (
        "\n@pytest.fixture\ndef idle():\n    time.sleep(0.6)\n\ndef test_c(idle):\n    pass\n"
    )
    r = planted(tmp_path, next_one, setup_ceiling=0.3)
    assert r.returncode == 1 and "test_c setup took" in r.stdout, r.stdout + r.stderr
    assert "test_a_first setup" not in r.stdout, r.stdout


def test_a_fixture_under_the_setup_ceiling_passes(tmp_path) -> None:
    r = planted(tmp_path, FIXTURE_SLEEPER, setup_ceiling=30)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_setup_ceiling_stretches_with_the_boxs_load(tmp_path) -> None:
    r = planted(tmp_path, FIXTURE_SLEEPER, setup_ceiling=0.3, load=4.0)
    assert r.returncode == 0, r.stdout + r.stderr
    r = planted(tmp_path, FIXTURE_SLEEPER, setup_ceiling=0.3, load=1.5)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "over its 0.3 s ceiling x1.5 for the box's load and speed" in r.stdout, r.stdout


def test_a_load_that_fell_before_the_setup_ended_still_stretches_its_ceiling(tmp_path) -> None:
    """The sampler starts before the first setup, not after it: a fixture that ran at load 64 on
    16 cores (4x) and ended as the box went quiet is judged by the load it ran in."""
    body = (
        "import os, pytest, time\n\n"
        "@pytest.fixture\ndef slow_fixture():\n"
        "    os.environ['FAKE_LOAD_PHASE'] = 'run'\n    time.sleep(0.6)\n"
        "    os.environ['FAKE_LOAD_PHASE'] = 'end'\n    time.sleep(0.05)\n\n"
        "def test_slow_setup(slow_fixture):\n    pass\n"
    )
    r = planted_load(tmp_path, body, setup_ceiling=0.3)
    assert r.returncode == 0, r.stdout + r.stderr
    quiet = body.replace("'run'", "'end'")  # the same fixture on a box that was quiet throughout
    r = planted_load(tmp_path, quiet, setup_ceiling=0.3)
    assert r.returncode == 1, r.stdout + r.stderr


def test_a_module_that_names_its_setup_ceiling_gets_it_whichever_test_is_first() -> None:
    """Which test first triggers a module fixture depends on the worker and the order, so a
    setup's own ceiling is keyed by the test FILE, never by a test id that moves."""
    for file in _drift_guard.SETUP_SLOW:
        assert (REPO_ROOT / file).is_file(), f"{file} is in SETUP_SLOW and does not exist"
        assert "::" not in file, f"{file}: key SETUP_SLOW by file, not by test"
    assert _drift_guard.setup_ceiling("tests/test_x.py::test_a") == _drift_guard.SETUP_CEILING_S
    for file, own in _drift_guard.SETUP_SLOW.items():
        assert _drift_guard.setup_ceiling(f"{file}::test_whichever") == own


def test_conftest_imports_every_hook_the_guard_defines() -> None:
    """A hook the guard defines but conftest does not import never runs: the setup ceiling and
    its sampler would be dead code that reads like a gate."""
    import conftest

    hooks = [n for n in vars(_drift_guard) if n.startswith("pytest_")]
    assert {"pytest_runtest_makereport", "pytest_runtest_setup"} <= set(hooks), hooks
    for name in hooks:
        assert getattr(conftest, name, None) is getattr(_drift_guard, name), name
