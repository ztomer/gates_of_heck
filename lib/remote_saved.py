"""lib/remote_saved.py -- what the Linux worker saved, per share and per land (GOH.REMOTESAVED).

THE GAP. The land records said a share was sent and how it ended (lib/landq_remote.py,
GOH.REMOTEMUX), never what it was worth: on 2026-11-23 nobody could say how many minutes the
workers save a land, and the coordinator read the LOCAL worker count by mistake. Only the enqueue's
pre-step was recorded at all; the lander's pre-land under the lock and the land gate's suite share
(lib/remote_gate.py) left nothing in the record.

ONE FIGURE PER SHARE, whichever step sent it (`figure`):

    where      enqueue (the enqueue's pre-step, GOH_LAND_PRE_ONLY), pre-land (the lander's, under
               the land lock), gate (the land gate's suite)
    host       the worker that ran it, from the shard's own first line (`remote-shard: host H`,
               tools/remote_shard.py), "?" when the shard never said
    tests      what it was sent: nodes for a pre-step, files for the gate
    outcome    remote | failed | fallback
    worker_s   the share's wall: its launch to the worker's exit (the gate's give-up, if late)
    mac_s      the Mac's own side over the same span: the launch to the Mac's run done
    mac_est_s  the Mac wall the sent tests would have cost (their Mac-serial seconds over the
               measured Mac width, lib/remote_lane.py `Rates`): the Mac-only estimate's extra
    rerun_s    the Mac's run of what the worker gave back (a failure there, a fallback)
    saved_s    (mac_s + mac_est_s) - (max(mac_s, worker_s) + rerun_s): the Mac-only estimate less
               what the step took; negative when the worker cost time (late, or fell back)
    t0         the launch, epoch seconds: one share counts once across a batch's records

THE CHANNEL. Each share prints its figure as one line, `remote share: {json}` (`say`), where its
step's log is read: the lander's pre-land into the land's log; the gate's suite into `<its
verdict record>.shares` (tests/_land_remote.py), which lib/land_gate.sh copies into the land's log;
the enqueue's beside its figures (lib/remote_lane.py `note`, the record's `pre_run.remote`). The
lander reads the land's log into the record's `remote_shares` (lib/landq_remote.py `shares`), and
`goh land --report` sums them per day (`summary`). Pinned by tests/test_remote_saved.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

PREFIX = "remote share: "
HOST = re.compile(r"remote-shard: host (\S+)")
LINE = re.compile(r"^.*?remote share: (\{.*\})\s*$", re.MULTILINE)


# A fallback's cause, from the shard's own words (tools/remote_shard.py): the first that matches.
CAUSES = (
    ("mux", ("mux_client", "mm_send_fd")),
    ("busy", (" busy: load",)),
    ("off-pin", ("off goh's pins",)),
    ("skips", ("skips differ",)),
    ("scratch", ("GB free, under", "GB, over its")),
    ("in-flight", ("run in flight",)),
    ("unreachable", ("unreachable (ssh", "did not answer")),
    ("dropped", ("did not finish",)),
    ("no-junit", ("wrote no junit", "junit does not parse", "remote pytest exited")),
    ("start", ("did not start", "did not sync")),
)


def cause(said: str) -> str:
    """Why a share fell back, as one word (GOH.REMOTEMUX: the land record counts them)."""
    return next((name for name, marks in CAUSES if any(m in said for m in marks)), "other")


def host(said: str) -> str:
    """The worker the shard named in its log (tools/remote_shard.py's first line), else "?"."""
    m = HOST.search(said)
    return m.group(1) if m else "?"


def figure(where: str, said: str, tests: int, outcome: str, t0: float, mac_end: float,
           worker_end: float, mac_est_s: float, rerun_s: float) -> dict:  # fmt: skip
    """One share's figure (module doc); every wall is clamped at 0, a clock that ran backwards
    measures nothing."""
    mac_s, worker_s = max(0.0, mac_end - t0), max(0.0, worker_end - t0)
    took = max(mac_s, worker_s) + max(0.0, rerun_s)
    return {"where": where, "host": host(said), "tests": tests, "outcome": outcome,
            "worker_s": round(worker_s, 1), "mac_s": round(mac_s, 1),
            "mac_est_s": round(max(0.0, mac_est_s), 1), "rerun_s": round(max(0.0, rerun_s), 1),
            "saved_s": round(mac_s + max(0.0, mac_est_s) - took, 1),
            "t0": round(t0, 3)}  # fmt: skip


def ended(junit: Path, now: float) -> float:
    """When the worker finished: its junit's write when it wrote one before NOW (a worker done
    before the Mac is waited for at once, and NOW is the Mac's end), else NOW."""
    try:
        st = junit.stat()
    except OSError:
        return now
    return min(now, st.st_mtime) if st.st_size else now


def whole(notes: list[dict]) -> dict:
    """One land record's figure of every share of its pre-step (lib/remote_feed.py): the worker's
    wall from the first launch to its last end, the rest summed, the worst outcome."""
    if len(notes) < 2:
        return dict(notes[0]) if notes else {}
    out, rank = dict(notes[0]), ("remote", "failed", "fallback")
    out["outcome"] = max((n["outcome"] for n in notes), key=rank.index)
    out["cause"] = next((n["cause"] for n in notes if n.get("cause")), None)
    out["fresh"] = any(n.get("fresh") for n in notes)
    for k in ("tests", "mac_est_s", "rerun_s", "saved_s"):
        out[k] = round(sum(n.get(k) or 0 for n in notes), 1)
    ends = [n["t0"] + n["worker_s"] for n in notes if "t0" in n]
    if ends and "t0" in out:
        out["worker_s"] = round(max(ends) - out["t0"], 1)
    return out


def line(fig: dict) -> str:
    return PREFIX + json.dumps(fig, sort_keys=True)


def say(fig: dict) -> None:
    print(line(fig), flush=True)


def keep(lines: Iterable[str], path: str) -> None:
    """The figure lines among LINES appended to PATH (the gate's `<record>.shares`); none, or no
    path, writes nothing. The instrument never fails the gate."""
    figs = [ln for ln in lines if ln.startswith(PREFIX)]
    if not figs or not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as out:
            out.write("".join(f"{ln}\n" for ln in figs))
    except OSError:
        pass


def of_log(text: str) -> list[dict]:
    """Every share figure a log carries, in order; a line that does not parse is skipped."""
    out = []
    for m in LINE.finditer(text):
        try:
            fig = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(fig, dict) and "saved_s" in fig:
            out.append(fig)
    return out


def summary(figs: Iterable[dict]) -> dict:
    """{"shares", "saved_s", "worker_s", "mac_est_s", "hosts": {host: n}, "where": {where: n}} over
    figures that carry a saving, each share once (its `where` and `t0`)."""
    seen: set[tuple] = set()
    out: dict = {"shares": 0, "saved_s": 0.0, "worker_s": 0.0, "mac_est_s": 0.0, "hosts": {},
                 "where": {}}  # fmt: skip
    for fig in figs:
        if not isinstance(fig, dict) or "saved_s" not in fig:
            continue
        key = (fig.get("where"), fig.get("t0"), fig.get("host"))
        if key in seen:
            continue
        seen.add(key)
        out["shares"] += 1
        for k in ("saved_s", "worker_s", "mac_est_s"):
            out[k] = round(out[k] + float(fig.get(k) or 0), 1)
        for k, name in (("hosts", "host"), ("where", "where")):
            v = str(fig.get(name) or "?")
            out[k][v] = out[k].get(v, 0) + 1
    for k in ("hosts", "where"):
        out[k] = dict(sorted(out[k].items()))
    return out


def said(s: dict) -> str:
    """The report's words for a summary; "" when no share carried a saving."""
    if not s.get("shares"):
        return ""
    hosts = ", ".join(f"{h} {n}" for h, n in s["hosts"].items())
    where = ", ".join(f"{w} {n}" for w, n in s["where"].items())
    return (
        f"  worker saved {s['saved_s'] / 60:.1f} min over {s['shares']} shares ({where}; "
        f"{hosts}): worker {s['worker_s'] / 60:.1f} min for "
        f"{s['mac_est_s'] / 60:.1f} min of Mac"
    )
