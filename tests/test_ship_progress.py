"""A `goh ship` of one slice of a multi-release item records progress, not done (LOOP.SHIPSLICE).

A private consumer's v5.23.0 shipped one slice of a multi-release item and its done step ran
`loopctl done ID --shipped`, refused only by accident. `--progress` makes the last step `loopctl
progress ID --shipped RESULT`, which appends the ship's line to the item as PROGRESS and leaves it
open; without it the closing ship records done (test_ship.py). The loopctl side is .loop
tests/test_ship_slice.py.
"""

from __future__ import annotations

import test_ship
from test_ship import _calls, _ship

shipped = test_ship.shipped  # the fixture, by assignment (an import would be shadowed by each arg)


def test_a_slice_records_progress_and_not_done(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", "--progress", "--bump", "patch")
    assert r.returncode == 0, r.stdout + r.stderr
    last = _calls(tmp_path)[-1]
    assert last[:2] == ["progress", "X.ONE"] and last[2] == "--shipped", last
    assert not any(c[0] == "done" for c in _calls(tmp_path))


def test_progress_needs_an_item(shipped, tmp_path):
    r = _ship(shipped, tmp_path, "--wait", "--progress")
    assert r.returncode == 2 and "--progress needs --item" in r.stderr, r.stdout + r.stderr
