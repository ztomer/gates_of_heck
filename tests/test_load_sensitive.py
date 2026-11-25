"""Every test whose verdict can move with the box's load says so (GOH.LOADEDSUBSET, GOH.LOADEDDROP).

tests/load_sensitive.py derives the set (and says what it does not see); the marker
`@pytest.mark.load_sensitive` carries it in the source, so the set is one selection away
(`tools/load.py N -- tools/pytest.sh -m load_sensitive` when a flake is chased) while the land gate
runs every test. These keep the two the same set: a test the derivation names without the mark is
red (a new waiting, sleeping or spawning test is declared, never slipped in), a mark the
derivation does not back must say why, and every load-only failure seen so far
(tests/load_history.json, each with where it was seen) is inside the set.

The post-land loaded run that once ran this set (tools/loaded_suite.sh) is gone (GOH.LOADEDDROP:
0 real catches in 13 runs), so this static half runs at COMMIT: the line below declares that it
reads the staged tests, and gates/staged_guards.sh runs it over the index whenever a commit stages
one (tests/test_loaded_drop.py keeps the directive).
"""

# goh-reads-staged: tests

from __future__ import annotations

import json
from pathlib import Path

import load_sensitive as ls
from conftest import REPO_ROOT

TESTS = Path(__file__).resolve().parent
# The census reads every tests/ module (lib/declared_reads.py).
TREE_READS = ("tests/*.py",)
HISTORY = TESTS / "load_history.json"


def _census(test_src: str, **others: str) -> ls.Census:
    return ls.Census({"test_x": test_src, **others})


def test_the_detector_sees_each_signal_and_nothing_else() -> None:
    """Calibration: one test per signal, each named by exactly that signal; the rest by none."""
    src = (
        "import os, subprocess, threading, time\n"
        "from timing_bounds import GiveUp, patience\n"
        "def test_sleep(): time.sleep(0.1)\n"
        "def test_clock(): t = time.monotonic()\n"
        "def test_wall(): t = time.time()\n"
        "def test_poll():\n    for _ in GiveUp(5): pass\n"
        "def test_spawn(): subprocess.Popen(['x'])\n"
        "def test_thread(): threading.Thread(target=print)\n"
        "def test_signal(): os.kill(1, 0)\n"
        "def test_wait(p): p.wait()\n"
        "def test_join(t): t.join(2)\n"
        "def test_deadline(): subprocess.run(['x', '--stall', 'n'])\n"
        "def test_env_deadline(): env = {'GOH_STEP_TIMEOUT': 'n'}\n"
        "def test_tight(): subprocess.run(['x'], timeout=10)\n"
        "def _run(timeout=20): subprocess.run(['x'], timeout=timeout)\n"
        "def test_tight_default(): _run()\n"
        "def _hook(cmd, timeout=15): return {'command': cmd, 'timeout': timeout}\n"
        "def test_a_timeout_that_is_data(): _hook('x')\n"
        "def test_a_hang_guard(): subprocess.run(['x'], timeout=600)\n"
        "def test_a_scaled_guard(): subprocess.run(['x'], timeout=patience(5))\n"
        "def test_a_string_join(): ', '.join(['a'])\n"
        "def test_a_plain_run(): subprocess.run(['x'], check=True)\n"
    )
    got = {k.split("::")[1]: v for k, v in _census(src).derived().items()}
    assert got == {
        "test_sleep": ["sleep"],
        "test_clock": ["clock"],
        "test_wall": ["clock"],
        "test_poll": ["poll"],
        "test_spawn": ["spawn"],
        "test_thread": ["thread"],
        "test_signal": ["signal"],
        "test_wait": ["wait"],
        "test_join": ["wait"],
        "test_deadline": ["deadline"],
        "test_env_deadline": ["deadline"],
        "test_tight": ["tight"],
        "test_tight_default": ["tight"],
    }, got


def test_a_lookup_with_a_default_is_no_wait_and_a_timed_get_still_is() -> None:
    """GOH.LOADSENSEGET: `seed.get(name, 0)` on a plain dict read as a bounded wait and made
    tests/test_git_init_ratchet.py::test_every_file_is_at_its_seed red at commit. A get or an
    acquire waits by its timeout, the keyword or the second argument after a literal block flag
    (`queue.get(True, 1)`, `lock.acquire(True, 1)`), never by a default value."""
    src = (
        "def test_dict_default(seed): seed.get('a', 0)\n"
        "def test_dict_default_float(seed): seed.get('a', 1.5)\n"
        "def test_lock_flag(lock): lock.acquire(False)\n"
        "def test_queue_timeout(q): q.get(timeout=1)\n"
        "def test_queue_positional(q): q.get(True, 1)\n"
        "def test_lock_positional(lock): lock.acquire(True, 2)\n"
        "def test_event_wait(ev): ev.wait(5)\n"
        "def test_communicate(p): p.communicate(None, 5)\n"
    )
    got = {k.split("::")[1]: v for k, v in _census(src).derived().items()}
    assert got == {
        "test_queue_timeout": ["tight", "wait"],  # a literal timeout under TIGHT_S is tight too
        "test_queue_positional": ["wait"],
        "test_lock_positional": ["wait"],
        "test_event_wait": ["wait"],
        "test_communicate": ["wait"],
    }, got


def test_a_module_api_is_known_by_its_module_not_its_name() -> None:
    """GOH.TIMINGDETECT: a wait, sleep or spawn is a call to a KNOWN API. A module's function is
    known by the module it is called on or imported from (`time.sleep`, `from time import sleep`,
    `import subprocess as sp; sp.Popen`), so `psutil.Process(pid)` (a handle, no thread), a fake
    clock's `clock.sleep(5)` and a fixture's `fake.monotonic()` wait for nothing."""
    src = (
        "import psutil, time, subprocess as sp\n"
        "from time import sleep as nap\n"
        "from psutil import Process\n"
        "def test_a_handle(): psutil.Process(1)\n"
        "def test_a_handle_imported(): Process(1)\n"
        "def test_a_fake_sleep(clock): clock.sleep(5)\n"
        "def test_a_fake_clock(fake): fake.monotonic()\n"
        "def test_a_real_sleep(): nap(1)\n"
        "def test_an_aliased_spawn(): sp.Popen(['x'])\n"
        "def test_a_patched_clock(): timing_bounds.time.monotonic()\n"
        "def test_a_method_signal(proc): proc.terminate()\n"
    )
    got = {k.split("::")[1]: v for k, v in _census(src).derived().items()}
    assert got == {
        "test_a_real_sleep": ["sleep"],
        "test_an_aliased_spawn": ["spawn"],
        "test_a_patched_clock": ["clock"],
        "test_a_method_signal": ["signal"],
    }, got


def test_a_helper_a_kit_and_a_fixture_carry_their_signal_to_the_test() -> None:
    src = (
        "import pytest, load_kit\n"
        "from claim_kit import settle\n"
        "def _helper(): _deeper()\n"
        "def _deeper(): load_kit.spin()\n"
        "@pytest.fixture\n"
        "def busy(): settle()\n"
        "def test_via_helper(): _helper()\n"
        "def test_via_kit_import(): settle()\n"
        "def test_via_fixture(busy): pass\n"
        "def test_via_conftest_fixture(gate_proc): pass\n"
        "class TestK:\n"
        "    def _go(self): load_kit.spin()\n"
        "    def test_via_self(self): self._go()\n"
        "def test_alone(): pass\n"
    )
    census = _census(
        src,
        load_kit="import time\ndef spin(): time.sleep(1)\n",
        claim_kit="def settle(p): p.wait()\n",
        conftest="import subprocess\ndef gate_proc(): subprocess.Popen(['g'])\n",
    )
    got = {k.split("::", 1)[1]: v for k, v in census.derived().items()}
    assert got == {
        "test_via_helper": ["sleep"],
        "test_via_kit_import": ["wait"],
        "test_via_fixture": ["wait"],
        "test_via_conftest_fixture": ["spawn"],
        "TestK::test_via_self": ["sleep"],
    }, got


def test_a_mark_is_read_on_a_function_a_class_and_a_module_with_its_reason() -> None:
    src = (
        "import pytest\n"
        "@pytest.mark.load_sensitive\n"
        "def test_bare(): pass\n"
        "@pytest.mark.load_sensitive(reason='a gate ps under 30 s')\n"
        "def test_why(): pass\n"
        "@pytest.mark.load_sensitive\n"
        "class TestC:\n"
        "    def test_in_class(self): pass\n"
        "def test_unmarked(): pass\n"
    )
    assert _census(src).marked() == {
        "tests/test_x.py::test_bare": "",
        "tests/test_x.py::test_why": "a gate ps under 30 s",
        "tests/test_x.py::TestC::test_in_class": "",
    }
    whole = "import pytest\npytestmark = [pytest.mark.load_sensitive]\ndef test_a(): pass\n"
    assert _census(whole).marked() == {"tests/test_x.py::test_a": ""}


def _unmarked(census: ls.Census) -> list[str]:
    marked = census.marked()
    return [
        f"{t}  ({', '.join(s)})" for t, s in sorted(census.derived().items()) if t not in marked
    ]


def _stale(census: ls.Census) -> list[str]:
    derived = census.derived()
    return [t for t, why in sorted(census.marked().items()) if t not in derived and not why]


def test_a_planted_unmarked_waiting_test_is_refused_and_a_reasonless_mark_is_stale() -> None:
    """Calibration of the two tree rules below on planted modules."""
    census = _census(
        "import pytest, time\n"
        "def test_new(): time.sleep(1)\n"
        "@pytest.mark.load_sensitive\n"
        "def test_marked_for_nothing(): pass\n"
        "@pytest.mark.load_sensitive(reason='the gate it runs has a deadline')\n"
        "def test_marked_with_a_reason(): pass\n"
    )
    assert _unmarked(census) == ["tests/test_x.py::test_new  (sleep)"]
    assert _stale(census) == ["tests/test_x.py::test_marked_for_nothing"]


def test_every_load_sensitive_test_carries_the_marker() -> None:
    """THE GATE: a test that sleeps, reads a clock, polls, spawns beside itself, signals, waits or
    names a deadline says so -- `@pytest.mark.load_sensitive` above it."""
    missing = _unmarked(ls.Census.of(TESTS))
    assert not missing, (
        f"load-sensitive, without @pytest.mark.{ls.MARK}: {len(missing)} -- bound each wait "
        "(timing_bounds.patience, GiveUp) and mark it (tests/load_sensitive.py says why each is "
        "named):\n  " + "\n  ".join(missing)
    )


def test_no_mark_without_a_signal_or_a_reason() -> None:
    """A mark the derivation does not back names a test load cannot move, unless the test
    has a deadline the scan cannot see; then the mark says which (`reason=`)."""
    stale = _stale(ls.Census.of(TESTS))
    assert not stale, (
        "marked load_sensitive, but nothing in the test or what it reaches can move with load, and "
        "the mark gives no reason= -- drop it or say why:\n  " + "\n  ".join(stale)
    )


def test_every_historical_load_failure_is_in_the_set() -> None:
    """Each load-only failure seen so far (lands on busy boxes, hosted runners, consumer pushes)
    names a test that still exists and is marked, so the set holds every one seen."""
    census = ls.Census.of(TESTS)
    marked = census.marked()
    entries = json.loads(HISTORY.read_text(encoding="utf-8"))
    assert entries, "tests/load_history.json is empty"
    exists = {f"tests/{m}.py::{t}" for m, mod in census.mods.items() for t in mod.tests}
    gone = [e["test"] for e in entries if e["test"] not in exists]
    assert not gone, f"history names tests that no longer exist: {gone}"
    outside = [f"{e['id']}: {e['test']}" for e in entries if e["test"] not in marked]
    assert not outside, "historical load-only failures outside the marked set:\n  " + "\n  ".join(
        outside
    )
    assert all(e.get("seen") for e in entries), "every history entry says where it was seen"


def test_the_land_gate_runs_everything_and_the_marker_is_registered() -> None:
    runner = (REPO_ROOT / "tools" / "pytest.sh").read_text(encoding="utf-8")
    assert f"-m {ls.MARK}" not in runner, "tools/pytest.sh (the land gate's suite) narrows itself"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'"{ls.MARK}:' in pyproject, "the marker is not registered (--strict-markers)"
