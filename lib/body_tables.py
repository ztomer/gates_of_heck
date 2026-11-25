"""lib/body_tables.py -- the document fields of eight goh.db tables, as the columns and child rows
schema file 0016 gave them (GOH.BODYCOLS): what lib/body_columns.py writes and reads by, data only.

    LANES FLAKES MISSES SHADOW AUDIT REQUESTS EARLY    a Table each; TABLES, all seven
    (`commit_gated` keeps its own two columns, `tree` and `at`: lib/small_stores.py)

A FIELD is `path` (its column has the name, dots as underscores) or `path=column`. `have` are keys
the table had a column for before 0016; `same` are keys kept only where a column does not give them
back; `always` are keys a reader gets though nothing was kept, where their guard is there ("" is
any row): [], {} and null are no rows and no column. A new key of a document needs its column here
and in the next schema file; until then it stays in `rest`, which `goh state check` names.
Pinned by tests/test_body_columns.py, beside 0016's own triggers.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spawn_columns import instant  # lib/, on the path from the line above

TEXT, REAL, INTEGER, BOOL = "TEXT", "REAL", "INTEGER", "BOOL"
Fields = dict[str, tuple[str, str]]  # the path in the document: (its column, its type)


def words(kind: str, said: str) -> Fields:
    pairs = (w.partition("=") for w in said.split())
    return {p: (col or p.replace(".", "_"), kind) for p, _, col in pairs}


@dataclass(frozen=True, eq=False)
class Kid:
    """A list, a map or one object of a document, as rows of `table`."""

    path: str
    table: str
    shape: str  # text (a list of it), list (of objects), map (key to text), one (an object)
    kind: str | None = None
    fields: Fields = field(default_factory=dict)
    full: bool = False  # its writer names every field, so a null one is read back as one


@dataclass(frozen=True)
class Table:
    name: str
    key: tuple[str, ...]
    fields: Fields
    stamp: str = "at"
    have: tuple[str, ...] = ()
    blank: tuple[str, ...] = ()  # the `have` columns a row without the key holds '' in
    tuples: tuple[tuple[str, tuple[str, ...], str], ...] = ()
    kids: tuple[Kid, ...] = ()
    same: dict[str, Callable[[dict, dict], bool]] = field(default_factory=dict)
    derived: Callable[[dict, dict], dict[str, Any]] = lambda _own, _doc: {}
    always: tuple[tuple[str, Any, str], ...] = ()


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).isoformat(timespec="seconds")


def at_is(stamp: str) -> Callable[[dict, dict], bool]:
    """The document's `at` is the row's STAMP, to the second."""

    def said(doc: dict, own: dict) -> bool:
        at = instant(doc["at"]) if isinstance(doc.get("at"), str) else None
        return at is not None and int(at) == int(own[stamp])

    return said


def get(doc: Any, path: str) -> Any:
    for k in path.split("."):
        doc = doc.get(k) if isinstance(doc, dict) else None
    return doc


def one(path: str) -> Callable[[dict, dict], bool]:
    return lambda doc, _own: type(get(doc, path)) is int and get(doc, path) == 1


# -- lane_starts: lane admission's reading per write spawn (hooks/claude/lane_room.py) -------------

_HOLDER = words(TEXT, "repo slot weight_from") | words(REAL, "since weight") | words(INTEGER, "pid")
_OWNER = words(TEXT, "repo") | words(REAL, "since") | words(INTEGER, "n=slot_n pid")
_PRE = "reading.presteps."


def _lane(own: dict, doc: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"at": iso(own["at"]), **({"v": 1} if "verdict" in doc else {})}
    reading = doc.get("reading")
    if isinstance(reading, dict) and "error" not in reading:
        out["reading.v"] = 1
        if "room" in reading:
            out["reading.room.why"] = list(doc.get("why") or [])
        if get(reading, "cpu_weight.in_flight") == 0:
            out["reading.cpu_weight.in_flight"] = 0  # no holder: the sum of none is a whole 0
    return out


LANES = Table(
    "lane_starts",
    ("n",),
    words(
        TEXT,
        "declared mode session_id=session subagent_type=asked_type tool_use_id verdict item owner "
        "reading.at=read_at reading.error=error reading.disk.path=disk_path "
        "reading.memory.source=mem_source reading.memory.low=mem_low reading.slots.dir=slots_dir "
        "reading.cpu.source=cpu_source reading.presteps.cap.from=pre_cap_from",
    )
    | words(
        REAL,
        "reading.cpu_weight.in_flight=weight_in_flight reading.cpu_weight.per_core=weight_per_core "
        "reading.disk.free_gib=disk_free_gib reading.load.per_core=load_per_core "
        "reading.cpu.idle_pct=cpu_idle_pct reading.cpu.window_s=cpu_window_s "
        "reading.room.settings.GOH_LANE_LOAD_PER_CORE=set_load_per_core "
        "reading.room.settings.GOH_LANE_MIN_FREE_GIB=set_min_free_gib "
        "reading.room.settings.GOH_LANE_MIN_IDLE_PCT=set_min_idle_pct",
    )
    | words(
        INTEGER,
        "reading.memory.avail_pct=mem_avail_pct reading.memory.swap_used_mb=swap_used_mb "
        "reading.memory.total_mb=mem_total_mb reading.load.cores=cores "
        "reading.slots.free=slots_free reading.slots.held=slots_held "
        "reading.slots.total=slots_total reading.waiting.long=waiting_long "
        "reading.waiting.short=waiting_short reading.waiting.pre=waiting_pre "
        "reading.cpu.samples=cpu_samples reading.presteps.total=pre_total "
        "reading.presteps.held=pre_held reading.presteps.in_flight=pre_in_flight "
        "reading.presteps.cap.value=pre_cap "
        "reading.room.settings.GOH_LANE_MAX_WAITING=set_max_waiting",
    )
    | words(BOOL, "reading.room.ok=room_ok reading.room.unread=room_unread"),
    tuples=(("reading.load.avg", ("load1", "load5", "load15"), REAL),),
    kids=(
        Kid("why", "lane_why", "text", "why"),
        Kid("reading.unread", "lane_why", "text", "unread"),
        Kid("reading.slots.holders", "lane_holders", "list", "slot", _HOLDER, full=True),
        Kid(f"{_PRE}holders", "lane_holders", "list", "pre", _HOLDER, full=True),
        Kid(f"{_PRE}live", "lane_owners", "list", "live", _OWNER),
        Kid(f"{_PRE}reaped", "lane_owners", "list", "reaped", _OWNER),
    ),
    same={
        "at": at_is("at"),
        "v": one("v"),
        "reading.v": one("reading.v"),
        "reading.room.why": lambda doc, _: get(doc, "reading.room.why") == doc.get("why"),
    },
    derived=_lane,
    always=(
        *((k, None, "verdict") for k in ("item", "owner", "session_id", "tool_use_id")),
        ("why", [], "verdict"),
        ("reading.memory.low", None, "reading.memory"),
        ("reading.cpu.idle_pct", None, "reading.cpu"),
        ("reading.cpu.window_s", None, "reading.cpu"),
        ("reading.unread", [], "reading.room"),
        ("reading.slots.holders", [], "reading.slots"),
        *((f"{_PRE}{k}", [], "reading.presteps") for k in ("holders", "live", "reaped")),
    ),
)

# -- flakes, selection_misses: the flake register, the selection-miss ledger -----------------------


def _flake(own: dict, doc: dict) -> dict[str, Any]:
    return {"test": own["test"], "at": iso(own["epoch"])} if "id" in doc else {}


FLAKES = Table(
    "flakes",
    ("n",),
    words(TEXT, "id=flake first second repo sha site log.first=log_first log.second=log_second")
    | words(TEXT, "filed")
    | words(REAL, "load"),
    stamp="epoch",
    have=("epoch",),
    same={
        "at": at_is("epoch"),
        "test": lambda doc, own: isinstance(doc.get("id"), str) and doc["test"] == own["test"],
    },
    derived=_flake,
    always=(("load", None, "id"),),
)
MISSES = Table(
    "selection_misses",
    ("n",),
    words(TEXT, "item kind sha source script step test")
    | words(INTEGER, "failed missed selected steps steps_missed")
    | words(BOOL, "miss pre_ran"),
    have=("day",),
    kids=(Kid("changed", "miss_files", "text"),),
    always=(("changed", [], "failed"), ("changed", [], "script")),
)

# -- reuse_shadow, reuse_audit: every compared sample, each path's record (lib/reuse_audit.py) -----

_NAMED = words(
    TEXT,
    "base bin checker closure config corpus entry env identity key=field_key node remote repo "
    "rust_identity selection sha step tools tree",
)
_KEYED = {f"fields.{k}": v for k, v in _NAMED.items()}
_SAMPLE = words(TEXT, "key fresh reused fresh_from again.fresh again.reused") | _KEYED
_WINDOW = words(INTEGER, "agreed compared disagreed flakes sampled start uses unbacked")
_EVIDENCE = _SAMPLE | words(INTEGER, "epoch") | words(BOOL, "repeated")


def _sampled(own: dict, _doc: dict) -> dict[str, Any]:
    """lib/reuse.py's clock is whole seconds; the column a document's `epoch` had before is REAL."""
    at = own["epoch"]
    return {"epoch": int(at)} if at == int(at) else {}


SHADOW = Table(
    "reuse_shadow",
    ("n",),
    words(TEXT, "outcome") | _SAMPLE | words(BOOL, "repeated"),
    stamp="epoch",
    have=("path", "epoch"),
    derived=_sampled,
)
AUDIT = Table(
    "reuse_audit",
    ("path",),
    words(TEXT, "state cause why fault enabled.fix=on_fix enabled.test=on_test enabled.why=on_why")
    | words(TEXT, "enabled.was.cause=was_cause enabled.was.fault=was_fault enabled.was.why=was_why")
    | words(INTEGER, "since enabled.epoch=on_epoch enabled.was.since=was_since")
    | {f"window.{k}": (f"window_{k}", INTEGER) for k in _WINDOW},
    have=("path",),
    kids=(
        Kid("closed", "reuse_windows", "list", None, _WINDOW),
        Kid("evidence", "reuse_evidence", "one", "now", _EVIDENCE),
        Kid("enabled.was.evidence", "reuse_evidence", "one", "was", _EVIDENCE),
    ),
    always=(
        ("closed", [], "window"),
        ("evidence", {}, "cause"),
        *((f"enabled.was.{k}", None, "enabled") for k in ("cause", "fault", "why", "since")),
        ("enabled.was.evidence", {}, "enabled"),
    ),
)

# -- requests, early: the landing queue's requests (lib/landq_state.py) ----------------------------

_RUN = "pre_run."
_REMOTE = "pre_run.remote."
_ASKED = (
    words(
        TEXT,
        "worktree remote branch summary enqueued_at lane slot slot.worktree slot.token no_pre "
        "pre.judged pre.base pre.keep pre_carry pre_verdict.state=pre_verdict pre_verdict.kind "
        "pre_verdict.why red_last main_red.sha pre_affected.ran pre_affected.took pre_affected.by "
        "pre_run.skipped cov_risk.said=cov_said cov_risk.head=cov_head cov_risk.base=cov_base",
    )
    | words(INTEGER, "pre.number pre.group superseded pre_affected.tests pre_run.workers")
    | words(INTEGER, "cov_risk.lines=cov_lines")
    | words(REAL, "at=marked_at pre_s slot_s pre_affected.lane_s")
    | {f"{_RUN}{k}": (f"pre_run_{k}", REAL) for k in ("wait_s", "run_s", "limit_s", "cpu_s")}
    | {f"{_REMOTE}{k}": (f"remote_{k}", TEXT) for k in ("outcome", "cause", "where", "host")}
    | {f"{_REMOTE}{k}": (f"remote_{k}", REAL)
       for k in ("worker_s", "mac_s", "mac_est_s", "rerun_s", "saved_s", "t0")}
    | {f"{_REMOTE}tests": ("remote_tests", INTEGER), f"{_REMOTE}fresh": ("remote_fresh", BOOL)}
)  # fmt: skip
_RUNS: dict[str, Any] = {
    "key": ("queue", "run"),
    "tuples": (("pre.born", ("pre_born_s", "pre_born_us"), INTEGER),),
    "kids": (
        Kid("main_red.nodes", "request_nodes", "text"),
        Kid("cov_risk.carry", "request_cov_carry", "list", None, words(TEXT, "file text why")
            | words(INTEGER, "line") | words(BOOL, "own measured")),
        Kid("cov_risk.files", "request_cov_files", "map", None),
    ),
    "always": (
        ("main_red.nodes", [], "main_red"),
        ("cov_risk.files", {}, "cov_risk.lines"),
        ("cov_risk.carry", [], "cov_risk.head"),
    ),
}  # fmt: skip
REQUESTS = Table(
    "requests",
    fields=_ASKED,
    stamp="filed_at",
    have=("item", "repo", "sha", "mode", "pid", "pid_stamp", "until"),
    blank=("repo", "sha", "mode"),
    **_RUNS,
)
EARLY = Table(
    "early",
    fields=words(TEXT, "item repo sha mode pid_stamp")
    | words(INTEGER, "pid")
    | words(REAL, "until")
    | _ASKED,
    **_RUNS,
)
TABLES = (LANES, FLAKES, MISSES, SHADOW, AUDIT, REQUESTS, EARLY)
