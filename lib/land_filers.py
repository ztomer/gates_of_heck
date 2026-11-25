"""lib/land_filers.py -- the items a landing files, in one call from the lander (tools/landq.py).

Each filer reads the landed outcome and its request and returns the fields it adds to the landing
record ({} when it files nothing); none raises. One entry point keeps the lander's own line count
off its cap as filers are added (GOH.CAPAHEAD).

    diffcov_carry.file_item   the land's own uncovered changed lines (GOH.DIFFCOVCARRY)
    cap_ahead.file_items      each file the land grew into its cap's band (GOH.CAPAHEAD)

The items a landing files, in the lander's one call (`tools/landq.py`): `diffcov_carry.file_item`,
then `cap_ahead.file_items`. Pinned by `test_diffcov_carry.py`.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cap_ahead  # lib/
import diffcov_carry  # lib/


def file_all(outcome: dict, req: dict | None, loopctl: str, item: str = "") -> dict:
    """Every filer's fields for this landing, in order."""
    out: dict = {}
    for filer in (diffcov_carry.file_item, cap_ahead.file_items):
        out.update(filer({**outcome, **out}, req, loopctl, item))
    return out
