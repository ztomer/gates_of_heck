"""The fault item a reuse path turned off files, and the paths whose filing failed (GOH.REUSEAUDIT,
split from lib/reuse_audit.py for GOH.REUSEOFFLANDED2; docs/reuse.md).

`file_fault` runs `loopctl file` (GOH_LOOPCTL and GOH_LOOP_QUEUE are the seams) with the item
naming its class as rules.class_first asks (`class_of: none`) and the fields its phase's
`requires` names (rules.phase_contracts: `metric`, `predicted`). A path turns off only while on, so
each off is a new instance: an id the queue already holds is an EARLIER off's item, done or open,
and the next free id is filed, naming it. `unfiled` lists the paths off with no filed item, for
SessionStart (hooks/claude/estate_advisories.py). Pinned by tests/test_reuse_audit.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reuse  # lib/, beside this file: the store's root and its path registry

FILE_TRIES = 20  # ids GOH.REUSEOFF<PATH>, then <PATH>2 .. <PATH>20: one per off of the path


def file_fault(p: reuse.ReusePath, cause: str, why: str, line: dict) -> str:
    """File the fault item (loopctl file); the outcome, as the state record names it. A path turns
    off only while on, so each off is a new instance (an enable came between): an id the queue
    already holds is an EARLIER off's item, done or open, never this one -- the next free id is
    filed, naming it (GOH.REUSEOFFLANDED2: the refusal read as filed, and nothing tracked it)."""
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    if not os.path.isfile(loopctl):
        return f"not filed: no loopctl at {loopctl}"
    base = "GOH.REUSEOFF" + "".join(c for c in p.name.upper() if c.isalnum())
    said = ""
    for n in range(1, FILE_TRIES + 1):
        iid = base + (str(n) if n > 1 else "")
        before = (base + (str(n - 1) if n > 2 else "")) if n > 1 else ""
        code, said = _file(loopctl, _fault_item(p, iid, before, cause, why, line))
        if code == 0:
            return f"filed {iid}"
        if f"{iid} already exists" not in said:
            return f"not filed: {said[-200:]}"
    return f"not filed: {base} .. {FILE_TRIES} all exist: {said[-120:]}"


def _fault_item(p: reuse.ReusePath, iid: str, before: str, cause: str, why: str,
                line: dict) -> dict:  # fmt: skip
    again = (f" It is off again: {before} was filed for an earlier off, and the path was enabled "
             "after it; that fix did not hold.") if before else ""  # fmt: skip
    ran = f" The fresh side was {line['fresh_from']}." if line.get("fresh_from") else ""
    return {"id": iid, "repo": "gates_of_heck", "status": "todo", "phase": "SPD", "depends": [],
            "source": "goh reuse audit (lib/reuse_audit.py)",
            "item": f"The reuse path {p.name!r} ({p.doc}) is OFF: {cause}: {why}.{again}{ran} "
            f"Both verdicts and the key: {json.dumps(line, sort_keys=True)}",
            # rules.phase_contracts (loopctl): the phase's `requires`, from what the audit knows --
            # the skip the path makes, and that it stays off until enabled (GOH.REUSEOFFLANDED3)
            **contract_fields(p),
            "done_state": f"the missing key input found and keyed, a test that misses on it, and "
            f"`reuse_audit.py enable {p.name} --fix SHA --test NODE`",
            # rules.class_first (loopctl): each path's off is its own instance, no open closure's
            "class_of": "none",
            "class_why": f"the reuse path {p.name!r} turned off on its own evidence ({cause}); its "
            "cause is that path's key or reader, found from this record"}  # fmt: skip


def contract_fields(p: reuse.ReusePath) -> dict:
    """The fields a phase's `requires` names (SPD and EC: `metric`, `predicted`), for any filer
    of a reuse path's item: the metric is the path's skip, the prediction its state after a fix."""
    at = ", ".join(p.sites) or p.store
    return {"metric": f"reuse path {p.name!r} ({at}): on or off; off re-runs, on every use, the "
            f"work it skips ({p.doc})",
            "predicted": f"off -> on after `reuse_audit.py enable {p.name} --fix SHA --test NODE`"
            ": the skip reused again, its window clean"}  # fmt: skip


def _file(loopctl: str, item: dict) -> tuple[int, str]:
    queue = os.environ.get("GOH_LOOP_QUEUE", "")
    argv = [sys.executable, loopctl, *(["--queue", queue] if queue else []), "file", "--json",
            json.dumps(item)]  # fmt: skip
    try:
        r = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120,
                           stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return r.returncode, (r.stdout + r.stderr).strip()


def unfiled() -> list[str]:
    """One line per path that is off with its fault NOT filed: read-only (no roll, no lock), for
    SessionStart (hooks/claude/estate_advisories.py), so a refused filing alerts someone."""
    out = []
    for name in reuse.PATHS:
        try:
            rec = json.loads((reuse.root() / "audit" / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fault = str(rec.get("fault") or "") if isinstance(rec, dict) else ""
        if rec.get("state") == "off" and not fault.startswith("filed "):
            out.append(f"{name} off ({rec.get('cause')}), fault {fault or 'not filed'}")
    return out
