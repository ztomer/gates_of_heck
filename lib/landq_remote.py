"""The pre-steps' worker shares (GOH.REMOTEMUX), split from lib/landq_report.py and
lib/land_prestep.py for their caps: `took` reads a pre-step's share into its land record's
`pre_run.remote` (lib/remote_lane.py `note`); `goh land --report` counts them per row, the
fallbacks by cause, and the shares that left a broken ssh multiplexer for a fresh connection
(lib/remote_ssh.py). Pinned by tests/test_landq_report.py.

WHAT THEY SAVED (GOH.REMOTESAVED, lib/remote_saved.py): `shares` reads every share figure the
land's log carries (the lander's pre-land, the gate's suite) into the record's `remote_shares`;
`of` hands the report those with the enqueue's, and `count` sums them per row (`saved`), each
share once across a batch's records. Pinned by tests/test_remote_saved.py.

`goh land --report`'s count of the pre-steps' worker shares (GOH.REMOTEMUX, split from
`landq_report.py` for its cap): each record's `pre_run.remote` is one share; a row's `remote` is
`{shares, fallbacks: {cause: n}, fresh}` and one line under it. Pinned by `test_landq_report.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

try:
    from lib import remote_saved
except ImportError:  # run with lib/ itself on the path (tools/landq.py)
    import remote_saved  # type: ignore[no-redef]


def of(rec: dict) -> dict | None:
    """A done record's enqueue share (`pre_run.remote`) with every share figure it carries under
    `figures` (the enqueue's and `remote_shares`), None when it carries neither."""
    pre = rec.get("pre_run")
    share = pre.get("remote") if isinstance(pre, dict) else None
    share = share if isinstance(share, dict) else None
    more = rec.get("remote_shares") if isinstance(rec.get("remote_shares"), list) else []
    figs = [f for f in (share, *more) if isinstance(f, dict) and "saved_s" in f]
    return None if share is None and not figs else {**(share or {}), "figures": figs}


def count(shares: Iterable[dict | None]) -> dict:
    """{"shares", "fallbacks": {cause: n}, "fresh"} over a row's enqueue shares, and `saved`
    (lib/remote_saved.py `summary`) over every figure, when one carries a saving."""
    sent = [s for s in shares if s]
    enq = [s for s in sent if s.get("outcome")]
    falls: dict[str, int] = {}
    for share in enq:
        if share.get("outcome") == "fallback":
            why = str(share.get("cause") or "other")
            falls[why] = falls.get(why, 0) + 1
    out = {"shares": len(enq), "fallbacks": dict(sorted(falls.items())),
           "fresh": sum(bool(share.get("fresh")) for share in enq)}  # fmt: skip
    saved = remote_saved.summary(f for s in sent for f in s.get("figures") or [])
    return {**out, "saved": saved} if saved["shares"] else out


def line(sh: dict) -> list[str]:
    """The row's lines under the table: the enqueue shares' outcomes and what every share saved;
    none when it sent no share."""
    out = []
    if sh.get("shares"):
        falls = sorted(sh["fallbacks"].items(), key=lambda kv: (-kv[1], kv[0]))
        why = " (" + ", ".join(f"{k} {v}" for k, v in falls) + ")" if falls else ""
        fell = sum(sh["fallbacks"].values())
        out.append(f"  remote shares: {sh['shares']}, fell back {fell}{why}, {sh['fresh']} on a "
                   "fresh ssh connection")  # fmt: skip
    saved = remote_saved.said(sh.get("saved") or {})
    return out + ([saved] if saved else [])


def shares(outcome: dict) -> dict:
    """{"remote_shares": [figure...]} read from the attempt's log, for the land record
    tools/landq.py writes; {} when it carries none."""
    try:
        text = Path(str(outcome.get("log") or "")).read_text(errors="replace")
    except OSError:
        return {}
    figs = remote_saved.of_log(text)
    return {"remote_shares": figs} if figs else {}


def took(figures: Path, out: dict) -> dict:
    """A pre-step's figures with the worker's share it left beside them (`<figures>.remote`,
    lib/remote_lane.py `note`) under `remote`, the file removed; `out` unchanged with none."""
    remote = Path(f"{figures}.remote")
    try:
        share = json.loads(remote.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        share = None
    remote.unlink(missing_ok=True)
    return {**out, "remote": share} if isinstance(share, dict) else out
