#!/usr/bin/env python3
"""agent_audit.py -- was each subagent's model choice wrong, in either direction? From the ledger.

`hooks/claude/agent_guard.py` writes one line per spawn (its declared model@effort and tags) and
one per outcome (tokens from the agent's transcript, the `Status:` its report opened with) to
`$XDG_CACHE_HOME/goh/agent-spawns.jsonl`. This reads them back:

* UNDER, per cheap-tier class (sonnet@high, opus@low): spawns that ended blocked or failed, or that
  a later `escalation` spawn in the same session re-ran (same description or first line). Each is
  a cheap choice the work did not fit.
* OVER, per opus class at medium or above: spawns that LANDED under the threshold -- the median
  tokens of the cheap tier's landed spawns. Work that cost what cheap-tier work costs was
  cheap-tier work; the declared trigger tag did not make it harder.

A class with fewer than MIN_N outcomes gets no verdict, and neither does a threshold drawn from
fewer than MIN_N: it says "insufficient data (n=...)" instead (model-choice-decision.md, "Still
unmeasured"). Pinned by tests/test_agent_audit.py.

    python3 scripts/agent_audit.py            # the report
    python3 scripts/agent_audit.py --json     # the same, machine-readable
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import info, ok, section, warn

sys.path.insert(0, str(ROOT / "hooks" / "claude"))
from agent_guard import ledger_path

MIN_N = 20
CHEAP = ("sonnet@high", "opus@low")
OPUS_UP = ("opus@medium", "opus@high", "opus@xhigh", "opus@max")
NOT_LANDED = {"blocked", "failed"}


def read(path: Path) -> tuple[list[dict[str, Any]], int]:
    """The ledger's records and how many lines could not be read (a torn write, a hand edit)."""
    records, bad = [], 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            records.append(rec)
        else:
            bad += 1
    return records, bad


def _tokens(outcome: dict[str, Any] | None) -> int | None:
    tokens = (outcome or {}).get("tokens")
    total = tokens.get("total") if isinstance(tokens, dict) else None
    return total if isinstance(total, int) else None


def audit(records: list[dict[str, Any]], min_n: int = MIN_N) -> dict[str, Any]:
    outcomes = {(r.get("session_id"), r.get("tool_use_id")): r
                for r in records if r.get("event") == "outcome"}  # fmt: skip
    spawns = [r for r in records if r.get("event") == "spawn" and not r.get("exempt")]
    for s in spawns:
        s["class"] = f"{s.get('model')}@{s.get('effort')}"
        s["outcome"] = outcomes.get((s.get("session_id"), s.get("tool_use_id")))

    def escalated(s: dict[str, Any]) -> bool:
        return any(
            "escalation" in (e.get("tags") or [])
            and e.get("session_id") == s.get("session_id")
            and str(e.get("time")) > str(s.get("time"))
            and (e.get("description") == s.get("description")
                 or e.get("first_line") == s.get("first_line"))
            for e in spawns
        )  # fmt: skip

    def row(s: dict[str, Any], why: str) -> dict[str, Any]:
        return {"time": s.get("time"), "first_line": s.get("first_line"), "why": why,
                "tokens": _tokens(s["outcome"])}  # fmt: skip

    under: dict[str, Any] = {}
    for cls in CHEAP:
        done = [s for s in spawns if s["class"] == cls and s["outcome"]]
        flagged = []
        for s in done:
            status = s["outcome"].get("status")
            if status in NOT_LANDED:
                flagged.append(row(s, status))
            elif escalated(s):
                flagged.append(row(s, "escalated"))
        enough = len(done) >= min_n
        under[cls] = {
            "n": len(done), "flagged": flagged if enough else [],
            "verdict": (f"{len(flagged)} of {len(done)} too cheap "
                        f"({round(100 * len(flagged) / len(done))}%)") if enough else None,
        }  # fmt: skip

    reference = [t for s in spawns if s["class"] in CHEAP and s["outcome"]
                 and s["outcome"].get("status") == "landed"
                 and (t := _tokens(s["outcome"])) is not None]  # fmt: skip
    threshold = int(statistics.median(reference)) if len(reference) >= min_n else None
    over: dict[str, Any] = {}
    for cls in OPUS_UP:
        done = [s for s in spawns if s["class"] == cls and s["outcome"]]
        enough = len(done) >= min_n and threshold is not None
        flagged = [row(s, "landed cheap") for s in done
                   if s["outcome"].get("status") == "landed"
                   and (t := _tokens(s["outcome"])) is not None and threshold is not None
                   and t < threshold]  # fmt: skip
        over[cls] = {
            "n": len(done), "flagged": flagged if enough else [],
            "verdict": (f"{len(flagged)} of {len(done)} landed under {threshold} tokens "
                        f"({round(100 * len(flagged) / len(done))}%)") if enough else None,
        }  # fmt: skip
    return {"under": under, "over": over,
            "threshold": {"tokens": threshold, "n": len(reference)}}  # fmt: skip


def render(report: dict[str, Any], min_n: int) -> None:
    section("UNDER -- a cheap-tier choice the work did not fit")
    for cls, r in report["under"].items():
        if r["verdict"] is None:
            info(f"{cls}: insufficient data (n={r['n']}); a verdict needs {min_n}")
            continue
        (warn if r["flagged"] else ok)(f"{cls}: {r['verdict']}")
        for f in r["flagged"]:
            info(f"  {f['time']}  {f['why']:<9}  {f['first_line']}")
    section("OVER -- an opus spawn that landed at cheap-tier cost")
    th = report["threshold"]
    if th["tokens"] is None:
        info(f"threshold (median of landed cheap-tier spawns): insufficient data (n={th['n']})")
    else:
        info(f"threshold: {th['tokens']} tokens, the median of {th['n']} landed cheap-tier spawns")
    for cls, r in report["over"].items():
        if r["verdict"] is None:
            info(f"{cls}: insufficient data (n={r['n']}); a verdict needs {min_n} and a threshold")
            continue
        (warn if r["flagged"] else ok)(f"{cls}: {r['verdict']}")
        for f in r["flagged"]:
            info(f"  {f['time']}  {f['tokens']:>9}  {f['first_line']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path, default=None, help="default: the hook's ledger")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--min-n", type=int, default=MIN_N, help=f"outcomes a verdict needs ({MIN_N})")
    args = ap.parse_args(argv)
    path = args.ledger or ledger_path()
    if not path.exists():
        info(f"no spawn ledger at {path} yet: agent_guard writes it on the first spawn")
        return 0
    records, bad = read(path)
    report = audit(records, args.min_n) | {"unreadable_lines": bad}
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        print()
        return 0
    render(report, args.min_n)
    if bad:
        warn(f"{bad} ledger line(s) could not be read and were skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
