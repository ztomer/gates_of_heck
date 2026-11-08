#!/usr/bin/env python3
"""Measure what two managed Claude Code settings cost, from subagent transcripts (GOH.READCAP, GOH.CACHETTL).

The values in `hooks/claude/registrations.json` `settings` are chosen from these numbers, not
guessed; docs/config.md "Managed Claude Code settings" records the 2026-11-08 run. Two reports,
both over `~/.claude/projects/**/subagents/**/*.jsonl` (`--projects DIR`), split at `--cut`
(the ISO time a setting landed) into a before and an after window (`--days` back from the cut):

* `reads` -- the size of every text Read result (images keep their own limits and are counted
  apart), whole-file vs ranged, and how many would go over each candidate token cap. Tokens are
  CHARACTERS / a ratio measured from the transcripts themselves: a user turn holding one large
  tool result grows the next API turn's input by that result's tokens (median over such turns).
* `ttl` -- for each window, its cost as it ran and the cost the OTHER subagent TTL would have had
  on the same turns. Cost units are the API price ratios (input 1, cache read 0.1, 5m write 1.25,
  1h write 2, output 5). A turn after a gap of 5 min to 1 h is where the two differ: under 1h it
  reads what the previous turn cached and writes only what is new; under 5m it re-writes all but
  the shared prefix (the median first-turn cache read of that window, which a cold agent still
  hits). A gap over 1 h misses under both.

    scripts/claude_cache_probe.py {reads|ttl} --cut ISO [--days N] [--projects DIR] [--json]

Exit 0; 2 on a bad argument. Pinned by tests/test_claude_cache_probe.py.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import info

PRICE = {"input": 1.0, "read": 0.1, "5m": 1.25, "1h": 2.0, "output": 5.0}
CAPS = (6000, 8000, 10000, 12000, 15000, 20000, 25000)


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def _records(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def transcripts(projects: Path) -> list[Path]:
    return sorted(p for p in projects.glob("**/subagents/**/*.jsonl"))


def api_turns(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per API response (a response split over records repeats its usage: counted once)."""
    seen: set[str] = set()
    rows = []
    for i, d in enumerate(recs):
        m = d.get("message") or {}
        u, mid = m.get("usage"), m.get("id")
        if d.get("type") != "assistant" or not u or not mid or mid in seen or "timestamp" not in d:
            continue
        seen.add(mid)
        cc = u.get("cache_creation") or {}
        w5 = (
            cc.get("ephemeral_5m_input_tokens", 0)
            if cc
            else u.get("cache_creation_input_tokens", 0)
        )
        w1 = cc.get("ephemeral_1h_input_tokens", 0)
        rows.append({"at": _ts(d["timestamp"]), "i": i, "w5": w5, "w1": w1,
                     "read": u.get("cache_read_input_tokens", 0), "input": u.get("input_tokens", 0),
                     "output": u.get("output_tokens", 0)})  # fmt: skip
    return rows


def _text(content: Any) -> str | None:
    """A tool result's text; None for one holding an image."""
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    if any(isinstance(b, dict) and b.get("type") == "image" for b in blocks):
        return None
    return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def _window(at: dt.datetime, cut: dt.datetime, since: dt.datetime) -> str | None:
    return None if at < since else ("after" if at >= cut else "before")


def reads(paths: list[Path], cut: dt.datetime, since: dt.datetime) -> dict[str, Any]:
    ratios: list[float] = []
    win: dict[str, dict[str, Any]] = {k: {"text": [], "images": 0, "errors": 0, "allow_large": 0,
                                          "all_results": 0} for k in ("before", "after")}  # fmt: skip
    for path in paths:
        recs = _records(path)
        turns = api_turns(recs)
        uses: dict[str, tuple[str, dict[str, Any]]] = {}
        for i, d in enumerate(recs):
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses[b.get("id", "")] = (b.get("name", ""), b.get("input") or {})
            if d.get("type") != "user" or "timestamp" not in d:
                continue
            k = _window(_ts(d["timestamp"]), cut, since)
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]
            for b in results:
                text = _text(b.get("content"))
                if k:
                    win[k]["all_results"] += len(text or "")
                name, args = uses.get(b.get("tool_use_id", ""), ("", {}))
                if name != "Read":
                    continue
                if text is not None and len(results) == 1 and len(text) > 20000:
                    before = [t for t in turns if t["i"] < i]
                    after = [t for t in turns if t["i"] > i]
                    if before and after:
                        grew = sum(after[0][f] for f in ("w5", "w1", "read", "input")) - sum(
                            before[-1][f] for f in ("w5", "w1", "read", "input", "output")
                        )
                        if grew > 0:
                            ratios.append(len(text) / grew)
                if not k:
                    continue
                if text is None:
                    win[k]["images"] += 1
                    continue
                win[k]["errors"] += bool(b.get("is_error"))
                win[k]["allow_large"] += bool(args.get("allow_large"))
                win[k]["text"].append((len(text), "offset" in args or "limit" in args))
    ratio = statistics.median(ratios) if ratios else 4.0
    out: dict[str, Any] = {"chars_per_token": round(ratio, 2), "ratio_samples": len(ratios)}
    for k, w in win.items():
        toks = sorted(round(n / ratio) for n, _ in w["text"])
        whole = sorted(round(n / ratio) for n, ranged in w["text"] if not ranged)
        total = sum(toks) or 1
        out[k] = {"reads": len(toks), "whole": len(whole), "ranged": len(toks) - len(whole),
                  "images": w["images"], "errors": w["errors"], "allow_large": w["allow_large"],
                  "share_of_text_results": round(sum(n for n, _ in w["text"]) / max(1, w["all_results"]), 3),
                  "whole_tokens": whole,
                  "caps": {c: {"over": sum(t > c for t in toks),
                               "tokens_over": round(sum(t - c for t in toks if t > c) / total, 3)}
                           for c in CAPS}}  # fmt: skip
    return out


def ttl(paths: list[Path], cut: dt.datetime, since: dt.datetime) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {"before": [], "after": []}
    for path in paths:
        prev = None
        for t in api_turns(_records(path)):
            cached = t["w5"] + t["w1"] + t["read"]
            k = _window(t["at"], cut, since)
            if k:
                gap = (t["at"] - prev["at"]).total_seconds() if prev else None
                rows[k].append({**t, "cached": cached, "gap": gap,
                                "new": max(0, cached - prev["cached"]) if prev else cached})  # fmt: skip
            prev = {**t, "cached": cached}
    out: dict[str, Any] = {}
    for k, rs in rows.items():
        firsts = [r["read"] for r in rs if r["gap"] is None]
        shared = statistics.median(firsts) if firsts else 0
        ran = "1h" if sum(r["w1"] for r in rs) > sum(r["w5"] for r in rs) else "5m"
        other = "5m" if ran == "1h" else "1h"
        cost = {ran: 0.0, other: 0.0}
        gaps = [r for r in rs if r["gap"] is not None and r["gap"] > 300]
        for r in rs:
            fixed = r["input"] * PRICE["input"] + r["output"] * PRICE["output"]
            cost[ran] += (
                fixed + r["read"] * PRICE["read"] + r["w5"] * PRICE["5m"] + r["w1"] * PRICE["1h"]
            )
            write, read = r["w5"] + r["w1"], r["read"]
            if r["gap"] is not None and 300 < r["gap"] <= 3600:
                if other == "1h":
                    write, read = r["new"], r["cached"] - r["new"]
                else:
                    read = min(shared, r["cached"])
                    write = r["cached"] - read
            cost[other] += fixed + read * PRICE["read"] + write * PRICE[other]
        writes = sum(r["w5"] + r["w1"] for r in rs) or 1
        out[k] = {"turns": len(rs), "ran": ran, "write_5m": sum(r["w5"] for r in rs),
                  "write_1h": sum(r["w1"] for r in rs), "gap_turns": len(gaps),
                  "gap_write_share": round(sum(r["w5"] + r["w1"] for r in gaps) / writes, 3),
                  "shared_prefix": shared, "cost": {p: round(c) for p, c in cost.items()},
                  "ran_vs_other": round(cost[ran] / cost[other] - 1, 4) if cost[other] else None}  # fmt: skip
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report", choices=("reads", "ttl"))
    ap.add_argument("--cut", required=True, help="ISO time the setting landed")
    ap.add_argument("--days", type=float, default=7.0, help="before-window length (days)")
    ap.add_argument("--projects", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        cut = _ts(args.cut)
    except ValueError:
        ap.error(f"--cut: not an ISO time: {args.cut}")
    if cut.tzinfo is None:
        cut = cut.replace(tzinfo=dt.UTC)
    since = cut - dt.timedelta(days=args.days)
    paths = transcripts(args.projects)
    result = (reads if args.report == "reads" else ttl)(paths, cut, since)
    if args.json:
        sys.stdout.write(json.dumps(result, indent=1, default=str) + "\n")
        return 0
    info(f"{args.report}: {len(paths)} subagent transcript(s), cut {cut.isoformat()}")
    for k in ("before", "after"):
        sys.stdout.write(f"{k}: {json.dumps(result[k], default=str)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
