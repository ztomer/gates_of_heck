"""`goh land --report`'s count of the pre-steps' worker shares (GOH.REMOTEMUX), split from
lib/landq_report.py for its cap: each land record's `pre_run.remote` (lib/remote_lane.py `note`)
is one share; a row counts the shares, the fallbacks by cause, and the shares that left a broken
ssh multiplexer for a fresh connection (lib/remote_ssh.py). Pinned by tests/test_landq_report.py.
"""

from __future__ import annotations

from collections.abc import Iterable


def of(rec: dict) -> dict | None:
    """A done record's worker share, None when its pre-step sent none."""
    pre = rec.get("pre_run")
    share = pre.get("remote") if isinstance(pre, dict) else None
    return share if isinstance(share, dict) else None


def count(shares: Iterable[dict | None]) -> dict:
    """{"shares", "fallbacks": {cause: n}, "fresh"} over a row's attempts."""
    sent = [s for s in shares if s]
    falls: dict[str, int] = {}
    for share in sent:
        if share.get("outcome") == "fallback":
            why = str(share.get("cause") or "other")
            falls[why] = falls.get(why, 0) + 1
    return {"shares": len(sent), "fallbacks": dict(sorted(falls.items())),
            "fresh": sum(bool(share.get("fresh")) for share in sent)}  # fmt: skip


def line(sh: dict) -> list[str]:
    """The row's one line under the table, none when it sent no share."""
    if not sh.get("shares"):
        return []
    falls = sorted(sh["fallbacks"].items(), key=lambda kv: (-kv[1], kv[0]))
    why = " (" + ", ".join(f"{k} {v}" for k, v in falls) + ")" if falls else ""
    fell = sum(sh["fallbacks"].values())
    said = f"  remote shares: {sh['shares']}, fell back {fell}{why}, {sh['fresh']} on a fresh ssh"
    return [said + " connection"]
