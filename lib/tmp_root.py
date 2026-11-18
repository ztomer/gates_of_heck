"""goh_tmp(): the ONE parent of every temp root goh makes, `$TMPDIR/goh.noindex` (GOH.TMPNOINDEX).

Spotlight never indexes a directory whose name ends in `.noindex`, and goh's pytest temp trees were
94% of what mds resolved during gates (2026-11-17, EST.MDSCRAWL). The shell twin is lib/tmp_root.sh;
tests/tmp_root_lint.py refuses a temp root in gates/, lib/ or tools/ made outside either.
"""

from __future__ import annotations

import os
import tempfile

NAME = "goh.noindex"


def goh_tmp() -> str:
    """The directory, made if missing; a TMPDIR that IS a goh.noindex is used as it is."""
    base = tempfile.gettempdir().rstrip("/") or "/"
    if os.path.basename(base) != NAME:
        base = os.path.join(base, NAME)
    os.makedirs(base, exist_ok=True)
    return base
