"""lib/prune_kept.py: what a tool keeps on failure is bounded -- the newest N, anything recent, and
nothing outside its own prefix."""

import os
import sys
import time

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
from prune_kept import prune  # noqa: E402


def test_the_newest_and_the_recent_are_kept_and_nothing_else_is_touched(tmp_path) -> None:
    old = time.time() - 7200
    for i in range(25):
        d = tmp_path / f"goh-local-ci.{i:02d}"
        d.mkdir()
        os.utime(d, (old + i, old + i))
    (tmp_path / "goh-local-ci.live").mkdir()  # touched now: a run in flight
    (tmp_path / "other.00").mkdir()
    os.utime(tmp_path / "other.00", (old, old))
    removed = prune(str(tmp_path), "goh-local-ci.", keep=20, idle=3600)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "goh-local-ci.live" in left and "other.00" in left
    # The newest 20 are the live one and the 19 newest old ones; the 6 oldest go.
    assert len([n for n in left if n.startswith("goh-local-ci.")]) == 20
    assert sorted(os.path.basename(r) for r in removed) == [
        f"goh-local-ci.{i:02d}" for i in range(6)
    ]
