"""The landing queue's state files, written and read ONE way (GOH.CIRED01310).

Every `<landq>/{pending,running,done,enqueuing}/*.json` is a whole JSON object or absent: `write`
puts it in place by one rename, so no reader sees half of it, and `read` answers None for a file
that is not there (moved to the next state dir between a glob and the read) or not whole (a writer
that did not come through here), so a reader re-reads it next tick and never crashes on one.

v0.139.10's hosted CI: tests/test_landq_look.py wrote its requests with `write_text` (a truncate,
then a write), and `goh land --wait` (lib/landq_look.py) read the done record in between: "Expecting
value: line 1 column 1 (char 0)". The lander wrote by rename already; the test's copy of the writer
did not, and only a 3-core runner under coverage was slow enough to land a 10 ms poll in the gap.
Pinned by tests/test_landq_state.py.
"""

from __future__ import annotations

import json
from pathlib import Path


def write(path: Path, data: dict, indent: int | None = 1) -> None:
    """DATA at PATH by one rename: a reader sees the old file, none, or this one whole."""
    tmp = path.with_name(f".{path.name}.tmp")  # a dot name: no `*.json` glob sees it
    tmp.write_text(json.dumps(data, indent=indent) + "\n")
    tmp.replace(path)


def read(path: Path) -> dict | None:
    """PATH's record; None when it is gone, not whole, or not an object: read it again later."""
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None
