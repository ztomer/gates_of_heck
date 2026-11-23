"""lib/landq_report.py: `goh land --report`, where the queue's wall time goes (GOH.LANDREPORT).

OWNER, 2026-11-17: "most of our wall time seems to be taken by gating and lanes stepping on each
other toes, is this correct?" The answer was measured by hand from the done/ records; this module
is that measurement as a command, read from `$GOH_LANDQ_DIR/done/*.json` and their logs.

ONE ATTEMPT is one done record. Its times:

    enqueued   the nanosecond prefix of the file name (`<ns>-<ITEM>.json`)
    started    the record's `started` (written by the lander since GOH.LANDREPORT); an OLDER
               record falls back to its log's birth time
    finished   the record's `finished`, else its `at`
    queue wait started - enqueued          gate  finished - started
    slot wait  the record's `slot_wait_s`, else parsed from the log ("taken after Ns", else the
               longest "waited 4m 30s for a gate slot")        run  gate - slot wait

A birth time is no start when the log was born BEFORE the request (a log a later attempt reused)
or AFTER the finish: that attempt's queue wait, gate and run are UNKNOWN, counted in `unknown`,
never summed as a negative or a guess. Rows are per repo and UTC day of the finish.

JSON shape (`--json`; docs/map.md): {"since", "unloaded_s", "rows": [row...], "totals": row,
"unclassified": [{item, repo, day, log}], "gate_steps": {step: n}}; a row is {"repo", "day",
"attempts", "landed", "red", "cause", "queue_wait_s", "gate_s", "slot_wait_s", "run_s", "load_avg",
"gates", "gates_per_landed", "k", "unknown", "remote"} (lib/landq_remote.py). `gates` is the land
gates run: one per attempt a gate judged (`gated`: never an enqueue pre-step's, a check's or a
conflict's red), but a batch's items (their `batch` {id, gates}, lib/landq_batch.py) share theirs.
`k` is the batch size the lander chose for each batch or lone land, counted: {k: batches}
(GOH.ADAPTBATCH). `cause` counts the row's reds by cause (lib/land_cause.py, GOH.PREPARITY: the
reading the hand count was made with). `unloaded_s` is the shortest green run in the window: what a
gate costs when nothing else is running. `selection` is the selection-miss ledger by day
(lib/selection_miss.py, GOH.SELECTAUDIT): {day: {judged, missed, tests, steps, full, full_missed}},
a red attempt judged against its diff's selection.

Pinned by tests/test_landq_report.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tui.lib import counted, err, info, ok, warn

try:
    from lib import land_cause, landq_remote, selection_miss
except ImportError:  # run with lib/ itself on the path (tools/landq.py)
    import land_cause  # type: ignore[no-redef]
    import landq_remote  # type: ignore[no-redef]
    import selection_miss  # type: ignore[no-redef]

TAKEN = re.compile(r"gate slot \d+/\d+ taken after (\d+)s")
WAITED = re.compile(r"waited (?:(\d+)m )?(\d+)s for a gate slot")
SKEW_S = 2.0  # a clock/filename rounding slack when a log "predates" its request
# The Rust coverage step's decision (gates/_rust_proven.sh rust_coverage_reuse), as
# lib/log_follow.py carries it into a land's log: `measuring -- WHY` or `reused -- ...`.
COVERAGE = re.compile(
    r"\[rust\] coverage: (measuring -- .*|reused -- .*|not measured at a land.*)$", re.MULTILINE
)


def slot_wait_s(log_text: str) -> float | None:
    """Seconds spent waiting for gate slots, read from a land's log; None when it never says.

    Every `taken after Ns` is a slot the run got (a land may take a short-lane slot, then a ship
    slot): they add. A run killed while still waiting has only the `waited 4m 30s ...` progress
    lines, the longest of which is the wait so far."""
    taken = [int(m) for m in TAKEN.findall(log_text)]
    if taken:
        return float(sum(taken))
    waited = [int(m or 0) * 60 + int(s) for m, s in WAITED.findall(log_text)]
    return float(max(waited)) if waited else None


# A slot taken, with the class it was asked as (GOH.SLOTPRIORITY): tools/load.py's "load: gate slot
# 1/2 taken after 12s (GOH_GATE_SLOTS, class land)" and lib/gate_slot.sh's "gate slot 1/2 taken
# after 12s waiting (class lane)" or, with no wait, "gate slot 1/2 taken (GOH_GATE_SLOTS, class
# work)".
TAKEN_CLASS = re.compile(r"gate slot \d+/\d+ taken (?:after (\d+)s)?[^\n]*?\bclass (\w+)\)")


def slot_waits_by_class(log_text: str) -> dict[str, float]:
    """Seconds waited for gate slots per class, read from a land's log: every slot taken whose line
    names its class, summed by class ({} when none does). The land profile shows each class's wait
    as its own step, so a land waiting behind a lower class cannot hide in one figure."""
    out: dict[str, float] = {}
    for secs, klass in TAKEN_CLASS.findall(log_text):
        out[klass] = out.get(klass, 0.0) + float(secs or 0)
    return out


def slot_waits(outcome: dict) -> dict:
    """`slot_wait_s` and `slot_wait_by_class` read from the attempt's log, for the land record
    tools/landq.py writes beside GOH.LANDSNAPSHOT's slot_s (GOH.SLOTPRIORITY); {} when the log
    names no slot taken with its class. The land profile's `slot wait` and each class's
    row under it, and the `land attempt` ledger row, are these fields."""
    try:
        text = Path(str(outcome.get("log") or "")).read_text(errors="replace")
    except OSError:
        return {}
    by = slot_waits_by_class(text)
    if not by:
        return {}
    return {"slot_wait_s": round(sum(by.values()), 1), "slot_wait_by_class": by}


def coverage_said(log_text: str) -> list[str]:
    """Each distinct coverage decision a land's log records, in order (GOH.BATCHPREFROM): the
    lander's line names why a gate measured, or that a land measures none (GOH.COVOFFLAND)."""
    return list(dict.fromkeys(m.strip() for m in COVERAGE.findall(log_text)))


def announce(outcome: dict, who: str) -> None:
    """The lander's lines for a recorded OUTCOME: its state and sha, then (a land) each coverage
    decision its gate's log records, so a gate that measured says why (GOH.BATCHPREFROM)."""
    (ok if outcome["state"] == "landed" else err)(
        f"{who}: {outcome['state']} @ {outcome['sha'][:8]}"
    )
    import flakes  # lib/: a pass on retry by its register entry, never "looks flaky"

    for line in flakes.said(outcome):
        (warn if outcome["state"] == "landed" else info)(f"{who}: {line}")
    if outcome.get("mode") != "land":
        return
    try:
        text = Path(outcome["log"]).read_text(errors="replace")
    except (OSError, KeyError):
        return
    for said in coverage_said(text):
        info(f"{who}: the gate's coverage: {said}")


def birth(path: Path) -> float | None:
    """A file's creation time: the start of a log nobody timestamped."""
    try:
        st = path.stat()
    except OSError:
        return None
    return getattr(st, "st_birthtime", None) or st.st_ctime


def _iso(ts: object) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        when = dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    return when.replace(tzinfo=when.tzinfo or dt.UTC).timestamp()


@dataclass
class Attempt:
    stem: str
    item: str
    repo: str
    state: str
    enqueued: float
    finished: float
    started: float | None = None
    slot_s: float | None = None
    load: float | None = None
    cause: str | None = None  # a red attempt's (lib/land_cause.py)
    log: str = ""  # its log's path: an unclassified red is reported with it
    step: str = ""  # a `gate-step` red's failed step (land_cause.detail)
    batch: tuple[str, int] | None = None  # (batch id, the gate runs it spent): GOH.LANDBATCH
    gated: bool = True  # a gate judged it (`gated`)
    k: int | None = None  # the batch size the lander chose (GOH.ADAPTBATCH)
    remote: dict | None = None  # its pre-step's worker share, `pre_run.remote`: GOH.REMOTEMUX

    @property
    def day(self) -> str:
        return dt.datetime.fromtimestamp(self.finished, dt.UTC).strftime("%Y-%m-%d")

    @property
    def queue_wait_s(self) -> float | None:
        return None if self.started is None else max(0.0, self.started - self.enqueued)

    @property
    def gate_s(self) -> float | None:
        return None if self.started is None else max(0.0, self.finished - self.started)

    @property
    def run_s(self) -> float | None:
        gate = self.gate_s
        return None if gate is None else max(0.0, gate - (self.slot_s or 0.0))


def _stem_parts(stem: str) -> tuple[float, str]:
    ns, _, item = stem.partition("-")
    return int(ns) / 1e9, item


def read_attempts(queue: Path, birth_of: Callable[[Path], float | None] = birth) -> list[Attempt]:
    """Every done record as an Attempt; `birth_of` is the log-birth seam (a test sets it)."""
    out: list[Attempt] = []
    recs: list[tuple[Path, dict]] = []
    for path in sorted((queue / "done").glob("*.json")):
        try:
            rec = json.loads(path.read_text())
            _stem_parts(path.stem)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            recs.append((path, rec))
    users: dict[str, int] = {}
    for _, rec in recs:
        users[str(rec.get("log") or "")] = users.get(str(rec.get("log") or ""), 0) + 1
    for path, rec in recs:
        enq, item = _stem_parts(path.stem)
        fin = _iso(rec.get("finished")) or _iso(rec.get("at"))
        if fin is None:
            continue
        log = Path(rec["log"]) if rec.get("log") else None
        start = _iso(rec.get("started"))
        slot = rec.get("slot_wait_s")
        if start is None:  # an older record: the log's birth, unless that is no start
            born = birth_of(log) if log else None
            shared = users.get(str(rec.get("log") or ""), 0) > 1
            if born is not None and enq - SKEW_S <= born <= fin and not shared:
                start = born
        if slot is None and start is not None and log is not None and log.is_file():
            slot = slot_wait_s(log.read_text(errors="replace"))
        load = rec.get("load_start")
        cause, batch, step = None, rec.get("batch"), ""
        if rec.get("state") != "landed":
            text = log.read_text(errors="replace") if log is not None and log.is_file() else ""
            cause = land_cause.classify(rec, text)
            step = land_cause.detail(text) if cause == "gate-step" else ""
        out.append(
            Attempt(
                stem=path.stem,
                item=item,
                repo=Path(str(rec.get("repo") or "?")).name,
                state=str(rec.get("state") or "?"),
                enqueued=enq,
                finished=fin,
                started=start,
                slot_s=float(slot) if isinstance(slot, int | float) else None,
                load=float(load) if isinstance(load, int | float) else None,
                cause=cause,
                log=str(log or ""),
                step=step,
                batch=_batch(batch),
                gated=gated(rec),
                k=rec.get("k") if isinstance(rec.get("k"), int) else None,
                remote=landq_remote.of(rec),
            )
        )
    return out


def gated(rec: dict) -> bool:
    """Whether a land gate judged the attempt: landed, or red by a gate's exit or timeout. An
    enqueue pre-step's, a check's or a conflict's red never reached one (GOH.ADAPTBATCH); a red
    naming no why (a record older than whys) is counted as gated, as it always was."""
    why = str(rec.get("why") or "")
    if rec.get("state") == "landed" or not why:
        return True
    return why.startswith(("exit ", "timed out")) and not why.startswith("exit 75")


def _batch(rec: object) -> tuple[str, int] | None:
    """A record's batch (GOH.LANDBATCH) as (id, gate runs), or None for a land judged alone."""
    return (str(rec.get("id")), int(rec.get("gates") or 0)) if isinstance(rec, dict) else None


def _row(repo: str, day: str, atts: list[Attempt]) -> dict:
    known = [a for a in atts if a.started is not None]
    loads = [a.load for a in atts if a.load is not None]
    landed = sum(a.state == "landed" for a in atts)
    # A batch's items share its gate runs (GOH.LANDBATCH): counted once, not once per item.
    shared = dict(a.batch for a in atts if a.batch)
    gates = sum(a.batch is None and a.gated for a in atts) + sum(shared.values())
    sizes = {(a.batch[0] if a.batch else a.stem): a.k for a in atts if a.k is not None}
    return {
        "repo": repo,
        "day": day,
        "attempts": len(atts),
        "landed": landed,
        "red": sum(a.state != "landed" for a in atts),
        "cause": _causes(atts),
        "queue_wait_s": round(sum(a.queue_wait_s or 0.0 for a in known), 1),
        "gate_s": round(sum(a.gate_s or 0.0 for a in known), 1),
        "slot_wait_s": round(sum(a.slot_s or 0.0 for a in known), 1),
        "run_s": round(sum(a.run_s or 0.0 for a in known), 1),
        "load_avg": round(sum(loads) / len(loads), 1) if loads else None,
        "gates": gates,
        "gates_per_landed": round(gates / landed, 2) if landed else None,
        "k": {k: list(sizes.values()).count(k) for k in sorted(set(sizes.values()))},
        "unknown": len(atts) - len(known),
        "remote": landq_remote.count(a.remote for a in atts),
    }


def _causes(atts: list[Attempt]) -> dict[str, int]:
    out: dict[str, int] = {}
    for a in atts:
        if a.state != "landed":
            out[a.cause or "unclassified"] = out.get(a.cause or "unclassified", 0) + 1
    return out


def build(attempts: Iterable[Attempt], since: str = "") -> dict:
    """The report: rows per (repo, day) from `since` (a UTC date, inclusive), totals, the unloaded
    reference."""
    window = [a for a in attempts if a.day >= since]
    groups: dict[tuple[str, str], list[Attempt]] = {}
    for a in sorted(window, key=lambda a: a.finished):
        groups.setdefault((a.day, a.repo), []).append(a)
    rows = [_row(repo, day, atts) for (day, repo), atts in sorted(groups.items())]
    green = [a.run_s for a in window if a.state == "landed" and a.run_s]
    steps: dict[str, int] = {}
    for a in window:
        if a.step:
            name = a.step.split(": ", 1)[0]
            steps[name] = steps.get(name, 0) + 1
    return {
        "since": since,
        "unloaded_s": round(min(green), 1) if green else None,
        "rows": rows,
        "totals": _row("all", "all", window),
        "unclassified": [  # a fault of lib/land_cause.py: each is named with the log to read
            {"item": a.item, "repo": a.repo, "day": a.day, "log": a.log}
            for a in sorted(window, key=lambda a: a.finished)
            if a.state != "landed" and a.cause == "unclassified"
        ],
        "gate_steps": steps,
    }


def _min(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds / 60:.0f}"


def render(rep: dict) -> str:
    cols = (
        "repo",
        "day",
        "att",
        "land",
        "red",
        "cause",
        "wait",
        "gate",
        "slot",
        "run",
        "load",
        "g/l",
    )
    lines = [" ".join(f"{c:>6}" if i > 1 else f"{c:<16}" for i, c in enumerate(cols))]
    for r in [*rep["rows"], rep["totals"]]:
        g = r["gates_per_landed"]
        causes = sorted(r["cause"].items(), key=lambda kv: (-kv[1], kv[0]))
        cause = causes[0][0] if causes else "-"  # the commonest; all of them on the next line
        load = "-" if r["load_avg"] is None else f"{r['load_avg']:.0f}"
        cells = (
            r["repo"], r["day"], r["attempts"], r["landed"], r["red"], cause,
            _min(r["queue_wait_s"]), _min(r["gate_s"]), _min(r["slot_wait_s"]), _min(r["run_s"]),
            load, "-" if g is None else f"{g:.1f}",
        )  # fmt: skip
        lines.append(" ".join(f"{c!s:>6}" if i > 1 else f"{c!s:<16}" for i, c in enumerate(cells)))
        if r["k"]:
            lines.append("  batch size k: " + ", ".join(f"{k} x{n}" for k, n in r["k"].items()))
        if causes:
            lines.append("  red by cause: " + ", ".join(f"{k} {v}" for k, v in causes))
        lines += landq_remote.line(r.get("remote") or {})
        if r is rep["totals"] and r["unknown"]:
            lines.append(
                f"  {counted(r['unknown'], 'attempt')} with no known start: not in the sums"
            )
    if rep.get("gate_steps"):
        lines.append("gate steps: " + ", ".join(f"{k} {v}" for k, v in rep["gate_steps"].items()))
    lines += [
        f"⚠ unclassified red {u['item']} ({u['repo']} {u['day']}): read {u['log'] or 'no log'}"
        for u in rep.get("unclassified") or []
    ]
    lines += _selection_lines(rep.get("selection") or {})
    ref = rep["unloaded_s"]
    lines.append(
        "minutes: wait = enqueue to start, gate = start to done = slot + run; "
        f"unloaded reference (shortest green run): {'-' if ref is None else f'{ref:.0f}s'}"
    )
    return "\n".join(lines)


def _steps(d: dict) -> str:
    """The gate steps the pre-step never ran that went red (GOH.MISSLEDGERGATE), when any."""
    return f", {counted(d['steps'], 'gate step')} not run" if d.get("steps") else ""


def _selection_lines(days: dict[str, dict]) -> list[str]:
    """The selection misses per day: reds whose failed tests the pre-step's selection did not
    choose, of the reds judged, and the idle full runs' shadow."""
    out = []
    for day, d in days.items():
        rate = f"{d['missed'] / d['judged']:.0%}" if d["judged"] else "-"
        out.append(f"selection misses {day}: {d['missed']} of {counted(d['judged'], 'red land')} "
                   f"judged ({rate}), {counted(d['tests'], 'test')} not chosen"
                   f"{_steps(d)}; full-run shadow: "
                   f"{d['full_missed']} of {d['full']}")  # fmt: skip
    return out


def timed(run_one: Callable[..., dict], *args: object) -> dict:
    """`run_one()`'s outcome plus what the report reads (GOH.LANDREPORT): when it started and
    finished (to the millisecond), the seconds its log says it waited for a gate slot, and the
    box's load at the start and the end. Additive: an older reader ignores the keys."""
    started, load_start = dt.datetime.now(dt.UTC), load1()
    return stamped(run_one(*args), started, load_start)


def stamped(outcome: dict, started: dt.datetime, load_start: float | None) -> dict:
    """OUTCOME with timed()'s keys, for a run that began at STARTED (a batch's items share it)."""
    finished = dt.datetime.now(dt.UTC)
    log = Path(outcome["log"]) if outcome.get("log") else None
    text = log.read_text(errors="replace") if log and log.is_file() else ""
    return {**outcome, "started": started.isoformat(timespec="milliseconds"),
            "finished": finished.isoformat(timespec="milliseconds"),
            "slot_wait_s": slot_wait_s(text) or 0.0,
            "load_start": load_start, "load_end": load1()}  # fmt: skip


def load1() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def add_arguments(mode: Any, ap: Any) -> None:
    mode.add_argument("--report", action="store_true", help="where the queue's wall time went")
    ap.add_argument("--since", metavar="DATE", default="", help="--report: from this UTC date")
    ap.add_argument("--json", action="store_true", help="--report: the table as JSON")


def run(queue: Path, since: str, as_json: bool) -> int:
    rep = build(read_attempts(queue), since)
    rep["selection"] = selection_miss.summary(selection_miss.read(queue), since)
    print(json.dumps(rep, indent=1) if as_json else render(rep))
    return 0


def default_queue() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")
