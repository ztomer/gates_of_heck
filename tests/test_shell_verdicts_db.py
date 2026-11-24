"""The goh binary's per-file verdicts are rows of goh-cache.db (GOH.VERDICTDB V2).

`~/.cache/goh/verdicts` held them as one JSON file each: 175,644 files, 1.3 GB, no bound. They are
rows of goh-cache.db's `shell_verdicts` (0003, 30 days and 200k rows), read and written by the
binary in-process (rusqlite, bundled SQLite: decision (f)1). The binary never migrates the file:
lib/goh_cache_db.py does, so Python and Rust open one file. A cache that cannot be opened is a
miss for every file, never a different result.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import db_bounds  # lib/, on the path above
import goh_cache_db


def test_0003_is_shell_verdicts_bounded_at_a_month_and_200k_rows(tmp_path) -> None:
    with goh_cache_db.connect(tmp_path / "c.db") as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(shell_verdicts)")]
        assert 3 in goh_cache_db.db_core.version(con)["applied"]
    assert cols == ["checker", "key", "record", "at"]
    b = db_bounds.bounds(goh_cache_db.SEAM)["shell_verdicts"]
    assert (b.days, b.rows, b.stamp_col) == (30, 200_000, "at")
