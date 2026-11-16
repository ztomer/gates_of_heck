"""No test bounds a wait by a literal wall clock unless the load-scaled helper carries it.

The class (GOH.CLASS.HARNESS): a fixed timeout or a sleep-then-assert, right on a quiet box and red
at load 110-180. The detector is tests/wall_clock_lint.py, which says what it reads and what it
does not; the remedy is `timing_bounds.patience(seconds)` (or `GiveUp`, `assert_sooner`).

The seed, tests/wall_clock_seed.json, is exactly the sites each file held when this landed
(`unreviewed`: nobody has yet read whether each is a give-up or the subject's own clock). A NEW
site is red, and so is an entry larger than its file's count -- a fixed site takes its exemption
with it -- so the list only shrinks (close-the-class). Empty is closed.

It runs at COMMIT, not only at push: the line below declares that it reads the staged tests, so
gates/staged_guards.sh runs it over the index whenever a commit stages one (GOH.LOADEDDROP: the
post-land loaded run is gone, and this static half is what stands between a new literal wait and
a land; tests/test_loaded_drop.py keeps the directive).
"""

# goh-reads-staged: tests

from __future__ import annotations

import json
from pathlib import Path

import pytest
import wall_clock_lint as lint

TESTS = Path(__file__).resolve().parent
SEED = TESTS / "wall_clock_seed.json"


def test_the_detector_sees_every_shape_and_nothing_else() -> None:
    """Calibration: each literal shape is one finding; the helper's spellings are none."""
    src = (
        "import subprocess, time\n"
        "from timing_bounds import patience, GiveUp\n"
        "T = 10\n"
        "def test_x(p, q, ev, th, n):\n"
        "    subprocess.run(['x'], timeout=3)\n"  # 5: a literal
        "    p.wait(timeout=T)\n"  # 6: a literal under a name
        "    p.communicate(timeout=2 * 5)\n"  # 7: arithmetic over literals
        "    ev.wait(5)\n"  # 8: positional wait
        "    th.join(2.0)\n"  # 9: positional join
        "    time.sleep(0.5)\n"  # 10: sleep standing for a state ...
        "    assert p.poll() is None\n"  # ... asserted next
        "    subprocess.run(['x'], timeout=patience(3))\n"
        "    p.wait(timeout=n)\n"
        "    ', '.join(q)\n"
        "    time.sleep(patience(0.5))\n"
        "    assert p.poll() is None\n"
        "    time.sleep(0.05)\n"
        "    p.poll()\n"
        "    GiveUp(10)\n"
    )
    assert lint.findings(src) == [
        (5, "timeout"),
        (6, "timeout"),
        (7, "timeout"),
        (8, "wait"),
        (9, "wait"),
        (10, "sleep_assert"),
    ]


def test_a_sleep_then_assert_is_seen_in_every_block() -> None:
    src = (
        "import time\n"
        "def test_y(p):\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        time.sleep(1)\n"
        "        assert p\n"
        "    for _ in range(2):\n"
        "        time.sleep(1)\n"
        "        assert p\n"
    )
    assert lint.findings(src) == [(6, "sleep_assert"), (9, "sleep_assert")]


@pytest.mark.load_sensitive
def test_no_test_bounds_a_wait_by_a_literal_beyond_the_seed() -> None:
    seed = json.loads(SEED.read_text(encoding="utf-8"))["unreviewed"]
    now = lint.census(TESTS)
    assert now, "the census read no test file: the detector or its glob is blind"
    grown = {f: (seed.get(f, 0), n) for f, n in now.items() if n > seed.get(f, 0)}
    assert not grown, (
        "a literal wall-clock bound in a test (use timing_bounds.patience(seconds), GiveUp or "
        f"assert_sooner) -- file: (seed, now): {grown}"
    )
    stale = {f: (n, now.get(f, 0)) for f, n in seed.items() if now.get(f, 0) < n}
    assert not stale, f"lower these seed entries to the file's count -- file: (seed, now): {stale}"
