"""agent_guard's lane-start rule: a write spawn starts only while the box has room (GOH.ADAPTLANES).

Every Claude session's spawns pass through agent_guard, so this is where the machine-wide lane
count becomes adaptive: a WRITE spawn (any judged spawn whose tier is not one of `lean_types`'
`read_only_tiers`; exempt types such as Explore are never asked) takes lib/capacity.py's reading --
the one `goh capacity --json` prints -- and its rule.

GOH_LANE_ADMIT picks what no room does:
  refuse  (the default) refuse the spawn as DEFERRED, naming what the reading lacked
  warn    admit, and tell the session why there was no room
  off     ask nothing, record nothing
REFUSE IS THE DEFAULT since GOH.ADAPTLANES2 (owner 2026-11-17, "Yes, make it enforce"): the
ceilings (GOH_LANE_*, docs/config.md "Adaptive lanes") were calibrated from this ledger joined with
what each admitted lane then did (landq done records, the timing ledger, `goh land --report`). A
refusal starts nothing and loses nothing: its text says what the reading lacked and that the
coordinator re-dispatches the spawn when `goh capacity` exits 0. Any other mode word is warn, said
as such.

AN OWNER-ASKED SPAWN IS DEFERRED, NOT FORCED (GOH.OWNERASKCAP, mistakes.json M5): a prompt line
opening `Owner-asked: <the owner's words>` used to admit with no room, and on 2026-11-22 that let
gate-heavy lanes start past a refusal until load reached 130 on 16 cores. The owner asking for the
work is not the owner asking for it to run at once: with no room, refuse mode DEFERS it (recorded
`owner-deferred`: nothing starts, and the coordinator starts it when `goh capacity` admits), warn
mode admits it (`owner`), and above the HARD CEILING -- load1 over HARD_LOAD_PER_CORE x the cores,
or CPU idle under HARD_MIN_IDLE_PCT -- it is refused in every mode (`owner-refused`), naming the
numbers. The marker must open its own line, so a brief that merely quotes it is a plain spawn. The
coordinator's caps on agents and lanes are not this rule's: it only ever adds a refusal.

A reading that cannot be taken, or has an unread part, ADMITS with a warning in every mode
(probe-first: the instrument is never the reason work stops). Each judged spawn is one row of
goh.db's `lane_starts` (lib/hook_ledgers.py, GOH.STATEDBS4: a 5 s wait, then spilled): the verdict
(`room`, `warned`, `refused`, `owner`, `owner-deferred`, `owner-refused`, `unread`), the reasons,
the mode, the queue item the prompt names (`Queue item <ID>`, so the weekly review joins a spawn
to its land without the transcript) and the whole reading.
Pinned by tests/test_agent_guard_lanes.py.

agent_guard's lane-start rule (GOH.ADAPTLANES): a write spawn (a judged tier that is not read-only)
asks `lib/capacity.py`'s reading whether the machine has room for another lane; `GOH_LANE_ADMIT`
`refuse` (default, GOH.ADAPTLANES2) defers it naming what the reading lacked, `warn` admits and
says why not, `off` asks nothing; an `Owner-asked:` prompt line is deferred the same way (refused
above the hard ceiling: load1 over 4x the cores or idle under 5%, GOH.OWNERASKCAP) and a reading
that fails admits with a warning. Each judged spawn is a line of goh.db's `lane_starts` (verdict,
reasons, queue item, the reading; `hook_ledgers.py`). Pinned by `test_agent_guard_lanes.py`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

MODES = ("warn", "refuse", "off")
DEFAULT_MODE = "refuse"
WARN = "warn"  # what an unknown mode word reads as
MAX_LINE = 4000  # a row's bytes: past it the reading's slot holders are dropped, never the verdict
OWNER = re.compile(r"^Owner-asked:[ \t]*(\S.*?)[ \t]*$", re.MULTILINE)
HARD_LOAD_PER_CORE = 4  # load1 over this many per core: no spawn starts, owner-asked or not
HARD_MIN_IDLE_PCT = 5  # CPU idle under this: the same
ITEM = re.compile(r"\bQueue item ([A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)+)\b")


@dataclass(frozen=True)
class Verdict:
    deny: str | None = None  # the refusal reason (refuse mode, no room)
    message: str | None = None  # what the session is told (warn, unread, a bad mode word)
    record: dict[str, Any] | None = None  # the ledger line's lane fields


def _append(line: dict[str, Any]) -> str | None:
    """One lane_starts row (lib/hook_ledgers.py); the warning to show when it was not written."""
    data = json.dumps(line, sort_keys=True, separators=(",", ":"))
    if len(data) >= MAX_LINE:  # the holders go first, never the verdict
        line = dict(line, reading={k: v for k, v in line["reading"].items() if k != "slots"})
    try:
        import hook_ledgers  # lib/: goh.db's hook ledgers (GOH.STATEDBS4)

        return hook_ledgers.append(hook_ledgers.LANES, line)
    except (OSError, RuntimeError) as e:  # RuntimeError: lib/lock_order.py's refusal
        return f"agent_guard: the lane ledger was not written ({e})."


def is_write(tier: str | None, read_only_tiers: list[str]) -> bool:
    return tier is not None and tier not in read_only_tiers


def owner_words(prompt: str) -> str | None:
    """The owner's words on a line opening `Owner-asked:`, else None (an empty marker is none)."""
    m = OWNER.search(prompt)
    return m.group(1) if m else None


def hard_ceiling(reading: dict[str, Any]) -> list[str]:
    """What of `reading` is past the hard ceiling, each with its numbers; [] when nothing is."""
    over = []
    load = reading.get("load") or {}
    avg, cores = load.get("avg") or [], load.get("cores") or 0
    if avg and cores and avg[0] > HARD_LOAD_PER_CORE * cores:
        over.append(f"load1 {avg[0]:g} over {HARD_LOAD_PER_CORE * cores} "
                    f"({HARD_LOAD_PER_CORE}x {cores} cores)")  # fmt: skip
    idle = (reading.get("cpu") or {}).get("idle_pct")
    if idle is not None and idle < HARD_MIN_IDLE_PCT:
        over.append(f"CPU {idle:g}% idle, under {HARD_MIN_IDLE_PCT}%")
    return over


def judge(event: dict[str, Any], agent_type: str, declared: str) -> Verdict:
    """The lane verdict for a write spawn; never raises (a failure admits, said)."""
    raw = os.environ.get("GOH_LANE_ADMIT", "").strip()
    mode = raw or DEFAULT_MODE
    notes = []
    if mode not in MODES:
        notes.append(f"GOH_LANE_ADMIT={raw!r} is not one of {', '.join(MODES)}: read as warn")
        mode = WARN
    if mode == "off":
        return Verdict()
    prompt = str((event.get("tool_input") or {}).get("prompt") or "")
    owner = owner_words(prompt)
    item = m.group(1) if (m := ITEM.search(prompt)) else None
    try:
        import capacity

        reading = capacity.reading()
        room, line = reading["room"], capacity.summary(reading)
    except Exception as e:  # noqa: BLE001 -- probe-first: a broken reading never refuses a spawn
        reading, room, line = {"error": repr(e)}, {"ok": True, "why": [], "unread": True}, repr(e)
    if room.get("unread"):
        verdict = "unread"
        unread = reading.get("unread") or [reading.get("error")]
        notes.append(f"the capacity reading could not be read ({'; '.join(map(str, unread))}); "
                     f"this spawn was admitted unjudged")  # fmt: skip
    elif room["ok"]:
        verdict = "room"
    elif owner and (hard := hard_ceiling(reading)):
        verdict = "owner-refused"
    elif owner:
        verdict = "owner-deferred" if mode == "refuse" else "owner"
    else:
        verdict = "refused" if mode == "refuse" else "warned"
    record = {
        "v": 1, "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "session_id": event.get("session_id"), "tool_use_id": event.get("tool_use_id"),
        "subagent_type": agent_type, "declared": declared, "mode": mode, "verdict": verdict,
        "item": item, "owner": owner,
        "why": room.get("why", []), "reading": reading,
    }  # fmt: skip
    if failed := _append(record):
        notes.append(failed)
    no_room = (
        f"no room for another write lane: {'; '.join(room.get('why', []))} ({line}; "
        f"`goh capacity` prints the reading, GOH_LANE_ADMIT={mode})"
    )
    if verdict == "refused":
        return Verdict(
            deny=f"Refused, deferred: {no_room}. Nothing was started; re-dispatch this spawn "
            f"unchanged once `goh capacity` exits 0 (a slot frees, a waiter clears or the load "
            f"drops). A spawn the owner asked for (a prompt line `Owner-asked: <the owner's own "
            f"words>`) is deferred the same way, never forced past the capacity.",
            message=" ".join(notes) or None,
            record={"verdict": verdict},
        )
    if verdict == "owner-refused":
        return Verdict(
            deny=f"Refused, above the hard ceiling: {'; '.join(hard)} -- {no_room}. An owner-asked "
            f"spawn ({owner}) is not started past it in any mode; the coordinator starts it once "
            f"the load drops and `goh capacity` exits 0.",
            message=" ".join(notes) or None,
            record={"verdict": verdict},
        )
    if verdict == "owner-deferred":
        return Verdict(
            deny=f"Deferred, owner-asked ({owner}): {no_room}. The owner asking for the work is "
            f"not the owner asking for it to start now: nothing was started; the coordinator "
            f"starts this spawn unchanged once `goh capacity` exits 0.",
            message=" ".join(notes) or None,
            record={"verdict": verdict},
        )
    if verdict == "warned":
        notes.insert(0, f"agent_guard: {no_room}; admitted (warn mode).")
    if verdict == "owner":
        notes.insert(0, f"agent_guard: {no_room}; admitted as owner-asked ({owner}).")
    return Verdict(message=" ".join(notes) or None, record={"verdict": verdict})
