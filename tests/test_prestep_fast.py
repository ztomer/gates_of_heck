"""The pre-step runs its CHEAP judges first, chosen by measured duration (GOH.PRESTEPFAILFAST).

BATCHWINDOW's enqueue (2026-11-18) ran 473 s and failed on one 5 ms judge,
tests/test_wiring.py::test_every_script_has_a_row_in_the_map: the judges phase is ~500 s of summed
tests and its verdict comes at its end. So `affected_tests.py --run` runs, before anything else,
the judges the suite's own duration cache measures cheapest (lib/fast_tests.py), serially: a red
there is refused in seconds, naming the test.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from test_landq import FAKE_LOOPCTL, _pending
from test_prestep_judges import MAP_JUDGE, PYTEST, _branch, _enqueue
from timing_bounds import assert_sooner

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import fast_tests as ft


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """test_prestep_judges.py's: a queue of the test's own; --no-start, so no lander runs."""
    fake = tmp_path / "loopctl.py"
    fake.write_text(FAKE_LOOPCTL)
    return hermetic_env(
        drop_git=True,
        GOH_LANDQ_DIR=str(tmp_path / "landq"),
        GOH_LOOPCTL=str(fake),
        FAKE_LOOPCTL_LOG=str(tmp_path / "loopctl.log"),
    )


SLOW_JUDGE = "import time\n\n\ndef test_every_thing_slowly():\n    time.sleep(60)\n"


def test_the_set_is_the_cheapest_measured_tests_within_the_budget() -> None:
    known = {
        "t.py::test_a": 0.01,
        "t.py::test_b[1]": 0.2,  # parameters sum to the function's cost
        "t.py::test_b[2]": 0.2,
        "t.py::test_c@grp": 0.05,  # an xdist group suffix is not part of the function
        "t.py::test_slow": 0.6,  # over EACH_S
        "t.py::test_gone": 0.0,  # cached, but no candidate: the file no longer defines it
    }
    cands = ["t.py::test_a", "t.py::test_b", "t.py::test_c", "t.py::test_slow", "t.py::test_new"]
    assert ft.pick(cands, known, budget=4.0, each=0.5) == (
        ["t.py::test_a", "t.py::test_b", "t.py::test_c"],
        0.46,
    )
    # the budget cuts cheapest-first: the 0.4 s function does not fit after the two cheap ones
    assert ft.pick(cands, known, budget=0.3, each=0.5) == (["t.py::test_a", "t.py::test_c"], 0.06)
    assert ft.pick(cands, {}, budget=4.0) == ([], 0.0)  # nothing measured: nothing chosen


def test_units_are_the_functions_the_file_defines_now(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_a():\n    pass\n\n\nclass TestK:\n    def test_b(self):\n        pass\n"
    )
    assert ft.units(tmp_path, ["tests/test_x.py"]) == [
        "tests/test_x.py::test_a",
        "tests/test_x.py::TestK::test_b",
    ]
    assert ft.units(tmp_path, ["tests/test_x.py::TestK", "tests/gone.py"]) == [
        "tests/test_x.py::TestK::test_b"
    ]


def test_goh_s_cheap_structural_judges_are_candidates_on_its_own_tree(tmp_path, monkeypatch):
    """The judges that cost lands in seconds are among the candidates, whatever the diff."""
    cands = ft.units(REPO_ROOT, list(at.affected(REPO_ROOT, [])))
    for judge in ("tests/test_wiring.py::test_every_script_has_a_row_in_the_map",
                  "tests/test_config_schema.py::test_config_schema_covers_keys"):  # fmt: skip
        assert judge in cands, judge


@pytest.mark.load_sensitive
def test_a_red_cheap_judge_is_refused_before_a_slow_judge_finishes(repo, tmp_path, env) -> None:
    """The map judge is measured at 5 ms, the slow judge at 60 s: both are judges, so before
    GOH.PRESTEPFAILFAST one run held both and its verdict came after the sleep."""
    cache = tmp_path / "xdg"
    (cache / "goh").mkdir(parents=True)
    (cache / "goh" / "test-durations.json").write_text(json.dumps({
        "tests/test_wiring.py::test_every_script_has_a_row_in_the_map": 0.005,
        "tests/test_slow_judge.py::test_every_thing_slowly": 60.0,
    }))  # fmt: skip
    files = {"docs/map.md": "| `old.py` | a row |\n", "lib/old.py": "", "lib/new.py": "",
             "tools/pytest.sh": PYTEST, "tests/test_wiring.py": MAP_JUDGE,
             "tests/test_slow_judge.py": SLOW_JUDGE}  # fmt: skip
    wt = _branch(repo, tmp_path, files)
    t0 = time.monotonic()
    done = _enqueue(env, wt, XDG_CACHE_HOME=str(cache))
    took = time.monotonic() - t0
    out = done.stdout + done.stderr
    assert done.returncode == 2 and "nothing enqueued" in out, out
    assert "tests/test_wiring.py::test_every_script_has_a_row_in_the_map" in out, out
    assert "no row in docs/map.md for: ['new.py']" in out, out
    assert_sooner(took, 10, 60, "a red fast judge's refusal")  # the slow judge sleeps 60 s
    assert _pending(tmp_path) == []


def test_the_set_reads_the_cache_the_suite_writes(tmp_path, monkeypatch) -> None:
    """tests/_schedule.py writes it (and stays importable alone, without lib/ on the path)."""
    import _schedule

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert ft.cache() == _schedule._cache()
    ft.cache().parent.mkdir(parents=True)
    ft.cache().write_text(json.dumps({"t.py::test_a": 0.25, "bad": "x"}))
    assert ft.load() == _schedule._load() == {"t.py::test_a": 0.25}
    ft.cache().write_text("[1]")
    assert ft.load() == {}
