"""The instrument's window is a reading only when it is whole (GOH.FSDROPVERDICT, lib/fs_events.py).

On 2026-11-23, at load 9-45, `fs_events.measure` over 400 planted dirs read 395 with dropped=2, and
the suite's churn tests read 148 and 3 and refused lands: the window ended a fixed 0.5 s after the
command, FlushSync flushes only what fseventsd has received, and fseventsd lags under load; and a
window with drops, which the module says undercounts, was judged by exact floors. These pin both
ends of the window to the stream's own delivery and make a dropped window no count at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import fs_events
from _fs_churn import unmeasured_skip

darwin = pytest.mark.skipif(sys.platform != "darwin", reason="FSEvents is macOS only")
WINDOWS = 3  # a dropped window is re-run this many times before the instrument is blamed
PLANTED = 400
PLANT = "import os, sys\nfor i in range(400):\n    os.mkdir(os.path.join(sys.argv[1], f'd{i}'))\n"


def _window(tmp_path: Path) -> Path:
    """A root of its own per window: fseventsd folds a reused path's events into pending ones."""
    return Path(tempfile.mkdtemp(prefix="window.", dir=tmp_path))


def _measured(tmp_path: Path, plant: list[str]) -> dict:
    """measure() `plant` + its root until a window is clean, at most WINDOWS times."""
    why = []
    for _ in range(WINDOWS):
        root = _window(tmp_path)
        _, rep = fs_events.measure([str(root)], [*plant, str(root)])
        if rep.get("unmeasured"):
            why.append(rep["unmeasured"])
        elif rep["dirs_created"] < PLANTED:  # fseventsd lost records with no flag: no reading
            why.append(f"read {rep['dirs_created']} of the {PLANTED} planted dirs")
        else:
            return rep
    unmeasured_skip(why)


def test_a_window_with_drops_is_unmeasured_never_a_count() -> None:
    tally = fs_events.Tally(["/T"])
    made = fs_events.ITEM_CREATED | fs_events.ITEM_IS_DIR
    for n in range(5):
        tally.add(f"/T/d{n}", made)
    tally.add("/T", fs_events.MUST_SCAN_SUBDIRS | fs_events.USER_DROPPED)
    rep = tally.report(1.0)
    assert "the instrument dropped events" in rep["unmeasured"], rep
    assert rep["dropped"] == 1, rep
    for count in ("events", "dirs_created", "per_test", "top_creators"):
        assert count not in rep, rep  # never a count: tools/pytest.sh records none
    clean = fs_events.Tally(["/T"])
    clean.add("/T/d0", made)
    rep = clean.report(1.0)
    assert "unmeasured" not in rep and rep["events"] == 1 and rep["dirs_created"] == 1, rep


@pytest.mark.load_sensitive
@darwin
def test_measure_waits_on_the_streams_delivery_not_a_sleep(tmp_path: Path) -> None:
    """The window ends when the stream has delivered past the event id taken at the command's
    end (its end mark), whatever fseventsd's lag: a fixed sleep records neither id."""
    rep = _measured(tmp_path, [sys.executable, "-c", PLANT])
    assert rep["dirs_created"] >= 400, rep
    assert rep["through_event_id"] > 0, rep
    assert rep["delivered_through"] > rep["through_event_id"], rep


@pytest.mark.load_sensitive
@darwin
def test_events_made_before_the_window_are_not_its_own(tmp_path: Path) -> None:
    """fseventsd lags: a stream started SINCE_NOW is still handed what was made before it."""
    why = []
    for _ in range(WINDOWS):
        root = _window(tmp_path)
        subprocess.run(
            [sys.executable, "-c", PLANT, str(root)], check=True, stdin=subprocess.DEVNULL
        )
        shutil.rmtree(root / "d0")
        _, rep = fs_events.measure([str(root)], ["true"])
        if not rep.get("unmeasured"):
            assert rep["events"] == 0, rep
            return
        why.append(rep["unmeasured"])
    unmeasured_skip(why)


@darwin
@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only mode")
def test_a_window_that_cannot_be_ended_is_unmeasured(tmp_path: Path) -> None:
    """No root takes the end mark: the drain cannot prove delivery, so the window is no count."""
    root = tmp_path / "ro"
    root.mkdir()
    root.chmod(0o555)
    try:
        _, rep = fs_events.measure([str(root)], ["true"])
    finally:
        root.chmod(0o755)
    assert "no root took the start mark" in rep["unmeasured"], rep
    assert "events" not in rep, rep
