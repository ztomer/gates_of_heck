#!/usr/bin/env python3
"""agent_audit.py -- was each subagent's model choice wrong, in either direction? From the ledger.

`hooks/claude/agent_guard.py` writes one line per spawn (its declared model@effort and tags) and
one per outcome (tokens from the agent's transcript, the report and the `Status:` it opened with)
to `$XDG_CACHE_HOME/goh/agent-spawns.jsonl`. This reads them back, judged by the rules file
(`hooks/claude/model_choice.json`, through `model_choice.py` -- no class or price is spelled here):

* LANDED: every outcome whose report says landed and names shas is checked against GitHub
  (`lib/landed_verify.py`): each sha on its repo's default branch, CI concluded success. A
  mismatch is `silent-failure`; no network or no CI run yet is `unverified`, never a pass.
* UNDER, per class of the rules file's `cheap` tier: spawns that ended blocked or failed, that a
  later `escalation` spawn in the same session re-ran (same description or first line), or whose
  `landed` was a silent failure. Each is a cheap choice the work did not fit -- a redo.
* OVER, per class of the `opus` and `escalation` tiers: spawns that LANDED under the threshold --
  the median tokens of the cheap tier's landed spawns.
* VERDICT, per cheap class: cheap first and escalate on a miss costs c + r*o per item, the
  escalation class from the start costs o, so the class holds while its redo rate r < 1 - c/o
  (c, o: mean USD per attempt, the ledger's tokens at the rules file's prices). A class that
  breaks it is a PROPOSED rule change with its numbers. Nothing here writes the rules file: rule
  changes are the owner's (2026-10-18).

A class with fewer than `verdict.min_n` outcomes gets no verdict, and neither does a threshold or a
cost drawn from fewer: "insufficient data (n=...)". Pinned by tests/test_agent_audit.py and
tests/test_agent_audit_verify.py.

    python3 scripts/agent_audit.py             # the report (asks GitHub about landed shas)
    python3 scripts/agent_audit.py --offline   # no network: landed shas are `unverified`
    python3 scripts/agent_audit.py --json      # the same, machine-readable
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import info, ok, section, warn

sys.path.insert(0, str(ROOT / "hooks" / "claude"))
sys.path.insert(0, str(ROOT / "lib"))
import estate_registry
import landed_verify
import model_choice
from agent_guard import ledger_path

NOT_LANDED = {"blocked", "failed"}
Key = tuple[Any, Any]


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


def _key(rec: dict[str, Any]) -> Key:
    return (rec.get("session_id"), rec.get("tool_use_id"))


def _cache_path() -> Path:
    return ledger_path().with_name("agent-verify.json")


def verify_landed(
    records: list[dict[str, Any]], root: Path, gh: landed_verify.Gh | None
) -> dict[Key, landed_verify.Verdict]:
    """A verdict per landed outcome that carries its report. Only `verified` is cached: a silent
    failure is asked again, since a sha pushed late becomes true."""
    cache_file = _cache_path()
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    before = len(cache)
    # The estate is the walked root UNIONED with the registry (BACKLOG 6.14): a repo goh was
    # installed into outside the root is a repo a landed sha can be in, and a sha nobody can
    # find reads as unverified -- which is the same shape as a silent failure.
    repos = sorted(set(landed_verify.git_repos(root)) | set(estate_registry.existing()))
    out: dict[Key, landed_verify.Verdict] = {}
    for rec in records:
        if rec.get("event") != "outcome" or rec.get("status") != "landed":
            continue
        report = rec.get("report")
        if not isinstance(report, str):
            out[_key(rec)] = landed_verify.Verdict("unverified", why="no report on the ledger line")
            continue
        name = f"{rec.get('session_id')}/{rec.get('tool_use_id')}"
        if name in cache:
            out[_key(rec)] = landed_verify.Verdict("verified", cache[name])
            continue
        v = landed_verify.verify(report, repos, gh)
        out[_key(rec)] = v
        if v.verdict == "verified":
            cache[name] = v.shas
    if len(cache) != before:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
        except OSError:
            pass  # the cache only saves gh calls
    return out


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def audit(
    records: list[dict[str, Any]],
    rules: model_choice.Rules,
    min_n: int,
    landed: dict[Key, landed_verify.Verdict] | None = None,
) -> dict[str, Any]:
    landed = landed or {}
    outcomes = {_key(r): r for r in records if r.get("event") == "outcome"}
    spawns = [r for r in records if r.get("event") == "spawn" and not r.get("exempt")]
    for s in spawns:
        s["class"] = f"{s.get('model')}@{s.get('effort')}"
        s["outcome"] = outcomes.get(_key(s))
        v = landed.get(_key(s))
        s["silent"] = v.why if v and v.verdict == "silent-failure" else None
        s["cost"] = rules.cost(s.get("model"), (s["outcome"] or {}).get("tokens"))
    cheap, opus_up = rules.classes("cheap"), rules.classes("opus", "escalation")

    def escalated(s: dict[str, Any]) -> bool:
        return any(
            "escalation" in (e.get("tags") or [])
            and e.get("session_id") == s.get("session_id")
            and str(e.get("time")) > str(s.get("time"))
            and (e.get("description") == s.get("description")
                 or e.get("first_line") == s.get("first_line"))
            for e in spawns
        )  # fmt: skip

    def redo_why(s: dict[str, Any]) -> str | None:
        status = s["outcome"].get("status")
        if status in NOT_LANDED:
            return status
        if s["silent"]:
            return "silent-failure"
        return "escalated" if escalated(s) else None

    def row(s: dict[str, Any], why: str) -> dict[str, Any]:
        return {"time": s.get("time"), "first_line": s.get("first_line"), "why": why,
                "tokens": _tokens(s["outcome"])}  # fmt: skip

    under: dict[str, Any] = {}
    for cls in cheap:
        done = [s for s in spawns if s["class"] == cls and s["outcome"]]
        flagged = [row(s, why) for s in done if (why := redo_why(s))]
        enough = len(done) >= min_n
        under[cls] = {
            "n": len(done), "flagged": flagged if enough else [], "redo": len(flagged),
            "verdict": (f"{len(flagged)} of {len(done)} too cheap "
                        f"({round(100 * len(flagged) / len(done))}%)") if enough else None,
        }  # fmt: skip

    reference = [t for s in spawns if s["class"] in cheap and s["outcome"]
                 and s["outcome"].get("status") == "landed"
                 and (t := _tokens(s["outcome"])) is not None]  # fmt: skip
    threshold = int(statistics.median(reference)) if len(reference) >= min_n else None
    over: dict[str, Any] = {}
    for cls in opus_up:
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

    verdicts, proposals = _verdicts(spawns, rules, min_n, under)
    return {"under": under, "over": over, "threshold": {"tokens": threshold, "n": len(reference)},
            "verdicts": verdicts, "proposals": proposals,
            "verification": _verification(spawns, landed)}  # fmt: skip


def _verdicts(
    spawns: list[dict[str, Any]], rules: model_choice.Rules, min_n: int, under: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    esc = rules.verdict["escalation_class"]
    o_costs = [
        s["cost"] for s in spawns if s["class"] == esc and s["outcome"] and s["cost"] is not None
    ]
    o = _mean(o_costs)
    verdicts: dict[str, Any] = {}
    proposals: list[str] = []
    for cls, u in under.items():
        costs = [
            s["cost"]
            for s in spawns
            if s["class"] == cls and s["outcome"] and s["cost"] is not None
        ]
        c = _mean(costs)
        n = u["n"]
        rate = u["redo"] / n if n else None
        enough = n >= min_n and len(costs) >= min_n and len(o_costs) >= min_n and c and o
        be = 1 - c / o if enough else None
        holds = (rate < be) if enough else None
        verdicts[cls] = {"n": n, "redo": u["redo"], "redo_rate": rate, "cost": c, "cost_n": len(costs),
                         "opus_class": esc, "opus_cost": o, "opus_n": len(o_costs),
                         "break_even": be, "holds": holds}  # fmt: skip
        if holds is False:
            proposals.append(
                f"{cls}: redo {rate:.0%} >= break-even {be:.0%} (1 - ${c:.3f}/${o:.3f} per attempt; "
                f"n={n}, {esc} n={len(o_costs)}) -- propose: route this work to {esc} from the start"
            )
    return verdicts, proposals


def _verification(spawns: list[dict[str, Any]], landed: dict[Key, Any]) -> dict[str, Any]:
    by_key = {_key(s): s for s in spawns}
    out: dict[str, Any] = {"verified": 0, "no_shas": 0, "silent_failure": [], "unverified": []}
    for key, v in landed.items():
        s = by_key.get(key, {})
        entry = {"time": s.get("time"), "class": s.get("class"), "first_line": s.get("first_line"),
                 "shas": [x[:9] for x in v.shas], "why": v.why}  # fmt: skip
        if v.verdict == "verified":
            out["verified"] += 1
        elif v.verdict == "no-shas":
            out["no_shas"] += 1
        else:
            out[v.verdict.replace("-", "_")].append(entry)
    return out


def render(report: dict[str, Any], min_n: int) -> None:
    v = report["verification"]
    section("LANDED -- each landed report's shas against GitHub and CI")
    info(f"verified {v['verified']}, no shas {v['no_shas']}, unverified {len(v['unverified'])}")
    for f in v["silent_failure"]:
        warn(f"silent-failure  {f['time']}  {f['class']}  {f['first_line']}: {f['why']}")
    for f in v["unverified"]:
        info(f"  unverified  {f['time']}  {f['first_line']}: {f['why']}")
    section("UNDER -- a cheap-tier choice the work did not fit")
    for cls, r in report["under"].items():
        if r["verdict"] is None:
            info(f"{cls}: insufficient data (n={r['n']}); a verdict needs {min_n}")
            continue
        (warn if r["flagged"] else ok)(f"{cls}: {r['verdict']}")
        for f in r["flagged"]:
            info(f"  {f['time']}  {f['why']:<14}  {f['first_line']}")
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
    section("VERDICT -- does each cheap class pay for itself (redo < 1 - cheap/opus cost)?")
    for cls, r in report["verdicts"].items():
        if r["holds"] is None:
            info(f"{cls}: insufficient data (n={r['n']}, priced {r['cost_n']}, "
                 f"{r['opus_class']} n={r['opus_n']}); a verdict needs {min_n} of each")  # fmt: skip
        else:
            (ok if r["holds"] else warn)(
                f"{cls}: redo {r['redo_rate']:.0%} vs break-even {r['break_even']:.0%}"
            )
    for p in report["proposals"]:
        warn(f"PROPOSED (the owner's to make; nothing applied): {p}")


def gh_command(offline: bool) -> landed_verify.Gh | None:
    if offline:
        return None
    cmd = os.environ.get("GOH_GH") or ("gh" if shutil.which("gh") else "")
    return landed_verify.Gh(cmd) if cmd else None


def run(ledger: Path, root: Path, offline: bool, min_n: int | None = None) -> dict[str, Any] | None:
    """The report over `ledger` (None when there is no ledger yet)."""
    if not ledger.exists():
        return None
    rules = model_choice.load()
    records, bad = read(ledger)
    landed = verify_landed(records, root, gh_command(offline))
    n = min_n if min_n is not None else int(rules.verdict["min_n"])
    return audit(records, rules, n, landed) | {"unreadable_lines": bad, "min_n": n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", type=Path, default=None, help="default: the hook's ledger")
    ap.add_argument("--root", type=Path,
                    default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
                    help="where the repos a landed sha may be in live")  # fmt: skip
    ap.add_argument("--offline", action="store_true", help="never ask GitHub: shas unverified")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--min-n", type=int, default=None, help="outcomes a verdict needs (rules file)")
    args = ap.parse_args(argv)
    path = args.ledger or ledger_path()
    report = run(path, args.root, args.offline, args.min_n)
    if report is None:
        info(f"no spawn ledger at {path} yet: agent_guard writes it on the first spawn")
        return 0
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        print()
        return 0
    render(report, report["min_n"])
    if report["unreadable_lines"]:
        warn(f"{report['unreadable_lines']} ledger line(s) could not be read and were skipped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
