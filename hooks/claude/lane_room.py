"""agent_guard's lane-start rule: a write spawn starts only while the box has room (GOH.ADAPTLANES).

Every Claude session's spawns pass through agent_guard, so this is where the machine-wide lane
count becomes adaptive: a WRITE spawn (any judged spawn whose tier is not one of `lean_types`'
`read_only_tiers`; exempt types such as Explore are never asked) takes lib/capacity.py's reading --
the one `goh capacity --json` prints -- and its rule.

GOH_LANE_ADMIT picks what no room does:
  warn    (the default) admit, and tell the session why there was no room
  refuse  refuse the spawn, naming the reading
  off     ask nothing, record nothing
WARN IS THE DEFAULT because the ceilings (GOH_LANE_*, docs/config.md) were set by judgment from
one day's numbers (2026-11-17: load 40-150 on 16 cores, a land waiting 11+ min for a slot), not
calibrated: a wrong ceiling in refuse mode would stop the owner's lanes. The ledger below gives the
weekly usage review the numbers; once a ceiling's warned spawns are seen to be the ones that
idled behind slots, the mode flips to refuse. Any other word is warn, said as such.

A reading that cannot be taken, or has an unread part, ADMITS with a warning in every mode
(probe-first: the instrument is never the reason work stops). Each judged spawn is one line of
`$XDG_STATE_HOME/goh/lane-starts.jsonl` (default `~/.local/state/goh/`): the verdict (`room`,
`warned`, `refused`, `unread`), the reasons, the mode and the whole reading.
Pinned by tests/test_agent_guard_lanes.py.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

MODES = ("warn", "refuse", "off")
DEFAULT_MODE = "warn"
MAX_LINE = 4000  # under PIPE_BUF: one O_APPEND write stays whole


@dataclass(frozen=True)
class Verdict:
    deny: str | None = None  # the refusal reason (refuse mode, no room)
    message: str | None = None  # what the session is told (warn, unread, a bad mode word)
    record: dict[str, Any] | None = None  # the ledger line's lane fields


def ledger_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "goh" / "lane-starts.jsonl"


def _append(line: dict[str, Any]) -> str | None:
    data = json.dumps(line, sort_keys=True, separators=(",", ":"))
    if len(data) >= MAX_LINE:  # the holders go first, never the verdict
        line = dict(line, reading={k: v for k, v in line["reading"].items() if k != "slots"})
        data = json.dumps(line, sort_keys=True, separators=(",", ":"))
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (data + "\n").encode())
        finally:
            os.close(fd)
    except OSError as e:
        return f"agent_guard: the lane ledger was not written ({e})."
    return None


def is_write(tier: str | None, read_only_tiers: list[str]) -> bool:
    return tier is not None and tier not in read_only_tiers


def judge(event: dict[str, Any], agent_type: str, declared: str) -> Verdict:
    """The lane verdict for a write spawn; never raises (a failure admits, said)."""
    raw = os.environ.get("GOH_LANE_ADMIT", "").strip()
    mode = raw or DEFAULT_MODE
    notes = []
    if mode not in MODES:
        notes.append(f"GOH_LANE_ADMIT={raw!r} is not one of {', '.join(MODES)}: read as warn")
        mode = DEFAULT_MODE
    if mode == "off":
        return Verdict()
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
    else:
        verdict = "refused" if mode == "refuse" else "warned"
    record = {
        "v": 1, "at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "session_id": event.get("session_id"), "tool_use_id": event.get("tool_use_id"),
        "subagent_type": agent_type, "declared": declared, "mode": mode, "verdict": verdict,
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
            deny=f"Refused: {no_room}. Start this lane when a slot frees or the load drops, or "
            f"finish a running lane first.",
            message=" ".join(notes) or None,
            record={"verdict": verdict},
        )
    if verdict == "warned":
        notes.insert(0, f"agent_guard: {no_room}; admitted (warn mode).")
    return Verdict(message=" ".join(notes) or None, record={"verdict": verdict})
