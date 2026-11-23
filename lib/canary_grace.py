"""The release canary's grace-end finding, its one owner (GOH.CANARYGRACE): what the canary records
under `grace_over` and how it says it (scripts/release_canary.py), and what `goh ship` carries from
that record into its result, its `--status` and its verify card (tools/ship.py, lib/ship_verify.py).

`crate::grace` holds new scanner shapes back until OVER_FROM; the canary runs HEAD's pass once more
at GOH_GRACE=over while the grace runs and NAMES each consumer the grace's end turns red.

THE PASS BINDS NEAR THE END (GOH.CANARYMISS). v0.149.2 shipped an unseeded scanner an hour before
the end; this pass named 37 consumers red at the end, as a warning, and every one refused its
pushes an hour later. From `WINDOW_S` before the end the release is judged at max(now, the end):
the pass's flips, or a consumer it could not judge, REFUSE it (`binding` in the record). A refusal
rather than a filed work item: the canary also runs where no queue exists (CI, a fresh clone), and
a refusal is the one form nobody has to remember to act on. The record:

    grace_over: {ran, count, turned_red: {repo: [{step, found}]}, errors, seconds, head_seconds,
                 over_from, why, binding}

`count` is None when no pass ran (`why` says why); `seconds` is the grace pass's runs summed over
the consumers, `head_seconds` the day's HEAD pass's, so their ratio is the pass's cost.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, err, info, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canary_sides import FLOOR_KEY  # lib/, beside this file

CANARY = Path("scripts") / "release_canary.py"  # a repo that ships it is a gates tree
WINDOW_S = 7 * 86400  # from this long before the grace end, its flips refuse the release


GRACE_END = re.compile(r"^pub const OVER_FROM: u64 = ([\d_]+);", re.MULTILINE)


def ends(goh: Path) -> list[int]:
    """Every grace end of the gates tree `goh`: crate::grace's shared one and each scanner's own
    (`pub const OVER_FROM` in any crates/goh/src file), sorted. The canary's pass reads the shared
    one alone; a scanner's own (locale-prefix, dead-port) ends on its own day (GOH.GRACEWATCH)."""
    found = set()
    for rs in sorted((Path(goh) / "crates" / "goh" / "src").rglob("*.rs")):
        try:
            text = rs.read_text(encoding="utf-8")
        except OSError:
            continue
        found.update(int(m.replace("_", "")) for m in GRACE_END.findall(text))
    return sorted(found)


def next_end(goh: Path, now: float) -> int | None:
    """The first grace end of `goh` still ahead of `now`, or None."""
    return next((e for e in ends(goh) if e > now), None)


def binds(over_from: int | None, now: float) -> bool:
    """Whether the grace pass's verdict is the release's: within WINDOW_S of the end, or after."""
    return over_from is not None and now >= over_from - WINDOW_S


def refuses(g: dict, binding: bool) -> bool:
    """Whether a binding grace record G refuses the release: a flip, or a consumer not judged."""
    return bool(binding and g.get("ran") and (g.get("turned_red") or g.get("errors")))


def grace_pass(mode: str, over_from: int | None, now: float) -> tuple[bool, str]:
    """(run HEAD once more at GOH_GRACE=over, why not). `auto` runs it while the grace runs: from
    its date the day's pass is the grace-over pass, and a tree with no grace has nothing to end."""
    if mode == "on":
        return True, ""
    if mode == "off":
        return False, "--grace off"
    if over_from is None:
        return False, "HEAD has no grace (crates/goh/src/grace.rs names no OVER_FROM)"
    if now >= over_from:
        return False, f"the grace ended {day(over_from)}: the day's pass judges it"
    return True, ""


def day(epoch: int | None) -> str | None:
    return None if epoch is None else dt.datetime.fromtimestamp(epoch, dt.UTC).date().isoformat()


def shown(found: list[str]) -> list[str]:
    """A step's first 3 findings, and every floor KEY=N it names (canary_sides.FLOOR_KEY)."""
    keys = [f for f in found[3:] if FLOOR_KEY.match(f"  {f} ")]
    return found[:3] + keys


def grace_end(over: str, then, secs: float, now) -> dict:
    """{turned_red, errors, seconds}: HEAD once more at the grace's end (`then`, a canary_sides
    Verdict, under the side labelled OVER), against its pass under the day (`now`). A step red only
    there is NAMED; nothing here refuses the release."""
    if then.error:
        return {"turned_red": [], "errors": [f"under {over}: {then.error}"], "seconds": secs}
    flips = [{"step": s, "found": shown(then.found.get(s, []))} for s in sorted(then.red - now.red)]
    starved = [
        f"under {over}: {s} gave no verdict ({why})"
        for s, why in sorted(then.timed_out.items())
        if s not in now.red
    ]
    return {"turned_red": flips, "errors": starved, "seconds": secs}


def empty() -> dict:
    return {"turned_red": {}, "errors": [], "seconds": 0.0, "head_seconds": 0.0}


def fold(acc: dict, where: str, got: dict) -> None:
    """One consumer's judge() result into the sweep's grace record."""
    acc["head_seconds"] += got.get("head_seconds", 0.0)
    g = got.get("grace")
    if not g:
        return
    if g["turned_red"]:
        acc["turned_red"][where] = g["turned_red"]
    acc["errors"] += [f"{where}: {e}" for e in g["errors"]]
    acc["seconds"] += g["seconds"]


def closed(acc: dict) -> dict:
    return {
        **acc,
        "ran": True,
        "count": len(acc["turned_red"]),
        "errors": sorted(acc["errors"]),
        "seconds": round(acc["seconds"], 1),
        "head_seconds": round(acc["head_seconds"], 1),
    }


def say(g: dict) -> None:
    """The canary's lines: named, a warning while the end is far off, an error once it binds."""
    if not g.get("ran"):
        return
    when = g.get("over_from") or "its date"
    say_it = err if g.get("binding") else warn
    for e in g["errors"]:
        say_it(f"release canary could not judge at the grace end: {e}")
    if not g["turned_red"]:
        ok(f"release canary: the grace end ({when}) turns no consumer red ({g['seconds']} s)")
        return
    n = counted(g["count"], "consumer")
    how = "REFUSED: it ends within 7 days" if g.get("binding") else "named, not refused"
    say_it(f"release canary: the grace end ({when}) turns {n} red at push ({how}):")
    for where, flips in sorted(g["turned_red"].items()):
        for f in flips:
            say_it(f"  {where}: {f['step']} -- seed or fix it before {when}")
            for line in f["found"]:
                info(f"      found: {line}")


def carry(res: dict, state: Path | None = None) -> None:
    """The canary record's grace finding for the shipped commit (the hand-off's or the landed one),
    into the ship result RES as `grace_over` {count, repos, over_from, canary_sha}. A gates tree
    with no record for either commit gets an honest `count: None` and why; any other repo, nothing.
    A finding already carried is kept: the release step's record beats the push step's absence."""
    if state is None:
        import estate_registry  # lib/'s: the file the canary writes (GOH_ESTATE_STATE)

        state = estate_registry.state_file()
    try:
        rec = json.loads(state.read_text(encoding="utf-8")).get("release_canary") or {}
    except (OSError, ValueError, AttributeError):
        rec = {}
    shas = {res.get("sha"), res.get("handoff_sha")} - {None, ""}
    g = rec.get("grace_over") or {}
    if rec.get("head_sha") in shas and g:
        res["grace_over"] = {
            "count": g.get("count"),
            "repos": {k: [f["step"] for f in v] for k, v in (g.get("turned_red") or {}).items()},
            "over_from": g.get("over_from"),
            "canary_sha": rec["head_sha"],
            **({"why": g["why"]} if g.get("why") else {}),
        }
    elif (Path(res["repo"]) / CANARY).is_file() and (res.get("grace_over") or {}).get(
        "count"
    ) is None:
        named = ", ".join(sorted(s[:8] for s in shas))
        res["grace_over"] = {"count": None, "why": f"no release canary record for {named}"}


def line(res: dict) -> str | None:
    """The one line a ship says about it (`--status`, the verify card), or None."""
    g = res.get("grace_over")
    if not g:
        return None
    if g.get("count") is None:
        return f"grace end: not judged ({g.get('why') or 'no record'})"
    when = g.get("over_from") or "its date"
    if not g["count"]:
        return f"grace end ({when}): the release canary found no consumer it turns red"
    named = "; ".join(f"{k} ({', '.join(v)})" for k, v in sorted(g["repos"].items()))
    return f"grace end ({when}) turns {counted(g['count'], 'consumer')} red: {named}"
