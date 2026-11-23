"""A land the queue judges alone until it lands once (GOH.REDALONE, GOH.NOPREBATCH).

WHY. An item whose last land attempt was red, or that was enqueued `--no-pre`, is the likeliest to
red its batch, and a red batch costs every member a bisect. Of 292 lands in the queue's records on
2026-11-23, 80 came right after a red of the item's own code (48 a gate's, 32 a pre-step's). Such a
land gates on its own (lib/landq_batch.py `alone`: the take stops before it), beside batches in the
second gate slot, until it lands once; GOH.ADAPTBATCH sizes the batch, this decides who joins one.

THE MARKS, put on the request at its enqueue, so every reader of a request (the take, the batch
window) sees them without reading the queue's records:

    no_pre     the `--no-pre` reason (lib/land_check.py `at_enqueue`, GOH.NOPREBATCH).
    red_last   `red_last(q, item)` (lib/landq_request.py `land_request`): the why of the item's
               newest record, when that record is red on the item's own code. A red that judged
               no code -- a stack conflict, a tip moved, a timeout, no verdict, a git failure --
               is no mark.

Pinned by tests/test_landq_alone.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import landq_state

# A red record whose why says it judged no code of the item: never a reason to gate alone.
NOT_ITS_CODE = ("conflict", "tip moved", "timed out", "no verdict", "git failed", "superseded")


def red_last(q: Path, item: str) -> str:
    """ITEM's newest record under Q's `done/` when it is red on the item's own code: its why's first
    line (or `red`); '' when it landed, judged no code, or there is none."""
    done = q / "done"
    want = f"{item.replace('/', '_')}.json"
    try:
        names = sorted(n for n in os.listdir(done) if n.partition("-")[2] == want) if item else []
    except OSError:
        return ""
    rec = landq_state.read(done / names[-1]) if names else None
    why = str((rec or {}).get("why") or "")
    if not rec or rec.get("state") != "red" or any(w in why for w in NOT_ITS_CODE):
        return ""
    return (why.splitlines() or ["red"])[0][:160]
