"""A release push's near-line counts warn; a blowup, a total and a slow file still fail
(lib/count_advisory.py; owner 2026-11-26, "Go ahead", after six release pushes went red with
9517 passed on counts at the line)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import hermetic_env
from timing_bounds import patience

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tests"))

import _fs_ceilings  # noqa: E402
import count_advisory  # noqa: E402
import test_work  # noqa: E402

CONF = test_work.config('{"new": {"cpu_s": 1.0, "procs": 5, "git_repos": 1, "cargo_builds": 0,'
                        ' "tree_walks": 0}, "named": {}}')  # fmt: skip
FS = {"new": {"events": 150, "bytes": 1000000}, "today": {"events": 2000, "bytes": 8000000},
      "named": {}}  # fmt: skip


def _row(**got) -> dict:
    return {"cpu_s": 0.1, "procs": 1, "git_repos": 0, "cargo_builds": 0, "tree_walks": 0, **got}


def _fs(events: int) -> dict:
    return {"tests/test_a.py": {"events": events, "bytes": 10, "events_test": "tests/test_a.py::t"}}


@pytest.mark.parametrize("got", [{"procs": 12}, {"tree_walks": 2}, {"cpu_s": 2.4}])
def test_a_count_at_the_line_warns_at_a_release_push(got: dict) -> None:
    found = test_work.faults({"tests/test_a.py::t": _row(**got)}, CONF)
    assert found and count_advisory.split(found, release=True) == ([], found)


@pytest.mark.parametrize("got", [{"procs": 50}, {"tree_walks": 3}, {"cpu_s": 10.0}])
def test_a_blowup_still_fails_at_a_release_push(got: dict) -> None:
    found = test_work.faults({"tests/test_a.py::t": _row(**got)}, CONF)
    assert found and count_advisory.split(found, release=True) == (found, [])


@pytest.mark.parametrize(("events", "advisory"), [(154, True), (303, True), (376, False)])
def test_file_events_at_the_line_warn_and_a_blowup_fails(events: int, advisory: bool) -> None:
    found = _fs_ceilings.faults(_fs(events), FS, set(), whole=False, measured=True)
    blocking, warned = count_advisory.split(found, release=True)
    assert (warned, blocking) == ((found, []) if advisory else ([], found))


def test_a_stale_name_warns_at_a_release_push() -> None:
    conf = test_work.config('{"new": {"cpu_s": 1.0, "procs": 5, "git_repos": 1, "cargo_builds": 0,'
                            ' "tree_walks": 0}, "named": {"tests/test_a.py": {"procs": 20,'
                            ' "why": "x"}}, "stale_runs": 1}')  # fmt: skip
    stale = test_work.stale({"tests/test_a.py::t": _row(procs=2)}, conf, {})
    fs = {"named": {"tests/test_a.py": {"events": 600}}, **{k: FS[k] for k in ("new", "today")}}
    named = _fs_ceilings.faults(_fs(10), fs, set(), whole=True, measured=True)
    assert (
        stale and named and count_advisory.split(stale + named, release=True) == ([], stale + named)
    )


def test_anywhere_else_every_fault_fails() -> None:
    found = test_work.faults({"tests/test_a.py::t": _row(procs=12)}, CONF)
    assert found and count_advisory.split(found, release=False) == (found, [])


def test_a_whole_run_total_or_an_unknown_line_fails_at_a_release_push() -> None:
    lines = ["a whole run's procs 40000 > 33150 (GOH.SUITETOTAL)", "something else"]
    assert count_advisory.split(lines, release=True) == (lines, [])


def test_the_mark_is_read_once_and_no_child_inherits_it() -> None:
    env = hermetic_env(**{count_advisory.VAR: "release", "PYTHONPATH": str(REPO / "lib")})
    code = ("import os, count_advisory as c; print(c.AT_RELEASE,"
            f" {count_advisory.VAR!r} in os.environ)")  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                         stdin=subprocess.DEVNULL, check=True, timeout=patience(60))  # fmt: skip
    assert out.stdout.split() == ["True", "False"]
