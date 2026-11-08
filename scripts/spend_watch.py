"""spend_watch.py -- the SPEND section of the weekly model-choice watch (GOH.SPENDWATCH).

What agents spend, per week, from Claude Code's own subagent transcripts
(`~/.claude/projects/**/subagents/agent-*.jsonl`, type from the `.meta.json` beside each), read
by `claude_cache_probe.api_turns` (one row per API response: a response is written as one record
per content block, each repeating its usage, so the usage counts once by `message.id`). Cost units
are the API price ratios of `claude_cache_probe.PRICE` (input 1, cache read 0.1, 5m write 1.25,
1h write 2, output 5). An agent belongs to the week its first API turn falls in.

Why not OpenTelemetry or ccusage alone (owner ruling 2026-11-08: consume them, build only the
specialised part): OTel (`monitoring-usage.md`) exports token counts from the day it is turned on,
to a collector, and nothing of the past; ccusage (MIT, `ccusage claude daily --json`) totals tokens
per day and model. Neither sees what a turn DID (a push, a test run, a Read and its size), which
every metric below but the cost shares needs. So ccusage is the reader's CALIBRATION: each run its
totals over the window's whole UTC days must equal the reader's over the same transcripts, or the
section says the instrument disagrees.

Metrics: cost-unit shares; median first-turn context per agent type and of write agents (an agent
that called Edit or Write); share of context spent from the first push/release turn on, by turn
kind; cache writes after an idle gap over 5 min, share of cost; Read result bytes and their share
of tool-result bytes; repeated tool calls; turns per agent; and ~/.claude/CLAUDE.md bytes.

The loop: a metric worse than the week before (both weeks with MIN_AGENTS agents, past its
tolerance) is FILED as a queue item through `loopctl file`; a landed efficiency item whose `metric`
is one of METRICS has its measured value set beside its `predicted` (`spend_check`: confirmed,
short or regressed) once 7 days have passed since it landed, and a regression is filed. Rule-table
changes stay proposals: nothing here writes `model_choice.json`. Pinned by tests/test_spend_watch.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_cache_probe as probe

CCUSAGE = "npx -y ccusage@20.0.48"  # a named release (house rule: never a moving tag)
MIN_AGENTS = 3
GAP_S = 300
WRITE_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
WAIT_TOOLS = {"Monitor", "ScheduleWakeup", "TaskOutput", "TaskStop"}
READ_TOOLS = {"Read", "Grep", "Glob"}
OWNER_WORDS = "we also need to make this entire process self-improving"
BASH_KINDS = (  # first match wins, in this order
    ("land/release", r"\bgit\s+push\b|land\.sh|\bgh\s+release\b|release\.sh|\bgoh\s+(ship|release)\b|cargo\s+publish"),
    ("tests/builds", (r"pytest|cargo\s+(test|build|clippy|nextest|check)|gate(_profile)?\.sh|\bmake\b|"
                     r"swift\s+(build|test)|xcodebuild|npm\s+(test|run)|\bruff\b|\bmypy\b|shellcheck")),
    ("verify/poll/logs", (r"\bgh\s+(run|pr|api)\b|\bgit\s+(log|status|show|diff|fetch|rev-parse)\b|\bsleep\b|"
                         r"\btail\b|\.log\b|launchctl|pgrep|\bps\b|loopctl\.py\s+verify|\bcurl\b")),
)  # fmt: skip
KIND_RANK = [
    "land/release",
    "tests/builds",
    "verify/poll/logs",
    "edit",
    "read",
    "other",
    "wait/report",
]
# Each metric the loop can judge: lower is better for all. `label` is the queue's `metric` text an
# efficiency item names it by; `tol` is (absolute, relative) past which a week counts as worse.
METRICS: dict[str, dict[str, Any]] = {
    "first_turn_write": {"label": "median first-turn context of write agents", "tol": (2000, 0.1)},
    "post_push_share": {"label": "share of agent context spent from first push/release on", "tol": (0.05, 0)},
    "gap_write_share": {"label": "cache writes after a >5 min gap, share of agent cost", "tol": (0.02, 0)},
    "read_share": {"label": "Read share of tool-result bytes", "tol": (0.05, 0)},
    "repeated_share": {"label": "share of tool calls repeating an earlier identical call", "tol": (0.03, 0)},
    "turns_per_agent": {"label": "median API turns per agent", "tol": (3, 0.2)},
    "claude_md_bytes": {"label": "global CLAUDE.md bytes / agent first-turn tokens", "tol": (200, 0.05)},
}  # fmt: skip


def _kind(tools: list[tuple[str, dict[str, Any]]]) -> str:
    kinds = []
    for name, inp in tools:
        if name == "Bash":
            cmd = str(inp.get("command", ""))
            kinds.append(next((k for k, rx in BASH_KINDS if re.search(rx, cmd)), "other"))
        elif name in WRITE_TOOLS:
            kinds.append("edit")
        elif name in READ_TOOLS:
            kinds.append("read")
        else:
            kinds.append("wait/report" if name in WAIT_TOOLS else "other")
    return min(kinds, key=KIND_RANK.index) if kinds else "wait/report"


def _agent(path: Path) -> dict[str, Any] | None:
    recs = probe._records(path)
    tools: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    names: dict[str, str] = {}
    calls: dict[str, str] = {}
    results: list[tuple[str, int]] = []
    for d in recs:
        m = d.get("message") or {}
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use" and d.get("type") == "assistant":
                inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                uid = str(b.get("id", ""))
                if uid not in names:
                    tools.setdefault(str(m.get("id")), []).append((str(b.get("name")), inp))
                    names[uid] = str(b.get("name"))
                    calls[uid] = f"{b.get('name')} {json.dumps(inp, sort_keys=True)}"
            elif b.get("type") == "tool_result":
                text = probe._text(b.get("content"))
                if text is not None:
                    results.append((names.get(str(b.get("tool_use_id")), ""), len(text.encode())))
    rows = probe.api_turns(recs)
    if not rows:
        return None
    turns, prev = [], None
    for r in rows:
        mid = str((recs[r["i"]].get("message") or {}).get("id"))
        gap = (r["at"] - prev).total_seconds() if prev else None
        prev = r["at"]
        write = r["w5"] * probe.PRICE["5m"] + r["w1"] * probe.PRICE["1h"]
        turns.append({"at": r["at"], "ctx": r["input"] + r["read"] + r["w5"] + r["w1"],
                      "input": r["input"] * probe.PRICE["input"], "read": r["read"] * probe.PRICE["read"],
                      "write": write, "output": r["output"] * probe.PRICE["output"],
                      "gap_write": write if gap is not None and gap > GAP_S else 0.0,
                      "kind": _kind(tools.get(mid, []))})  # fmt: skip
    try:
        meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
        kind = str(meta.get("agentType") or "unknown")
    except (OSError, ValueError, AttributeError):
        kind = "unknown"
    seq = list(calls.values())
    return {"type": kind, "start": turns[0]["at"], "turns": turns, "results": results,
            "writes": any(n in WRITE_TOOLS for n in names.values()),
            "calls": len(seq), "repeated": len(seq) - len(set(seq))}  # fmt: skip


def agents(projects: Path, since: dt.datetime) -> list[dict[str, Any]]:
    """Every subagent whose transcript was written since `since`."""
    out = []
    for path in probe.transcripts(projects):
        try:
            if dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC) < since:
                continue
        except OSError:
            continue
        a = _agent(path)
        if a is not None:
            out.append(a)
    return out


def _r(x: float) -> float:
    return round(x, 4)


def week(all_agents: list[dict[str, Any]], lo: dt.datetime, hi: dt.datetime) -> dict[str, Any]:
    ags = [a for a in all_agents if lo <= a["start"] < hi]
    turns = [t for a in ags for t in a["turns"]]
    cost = {k: sum(t[k] for t in turns) for k in ("read", "write", "output", "input")}
    total = sum(cost.values()) or 1.0
    ctx = sum(t["ctx"] for t in turns) or 1
    post: dict[str, float] = {}
    for a in ags:
        first = next((i for i, t in enumerate(a["turns"]) if t["kind"] == "land/release"), None)
        for t in a["turns"][first:] if first is not None else []:
            post[t["kind"]] = post.get(t["kind"], 0) + t["ctx"]
    by_type: dict[str, list[int]] = {}
    for a in ags:
        by_type.setdefault(a["type"], []).append(a["turns"][0]["ctx"])
    writers = [a["turns"][0]["ctx"] for a in ags if a["writes"]]
    res = [r for a in ags for r in a["results"]]
    read = sum(n for name, n in res if name == "Read")
    calls = sum(a["calls"] for a in ags)
    med = lambda xs: statistics.median(xs) if xs else None
    out = {"agents": len(ags), "turns": len(turns), "calls": calls,
            "cost_units": round(sum(cost.values())),
            "cost_share": {k: _r(v / total) for k, v in cost.items()},
            "first_turn": {k: med(v) for k, v in sorted(by_type.items())},
            "first_turn_write": med(writers),
            "post_push_share": _r(sum(post.values()) / ctx),
            "post_push_by_kind": {k: _r(v / ctx) for k, v in sorted(post.items())},
            "gap_write_share": _r(sum(t["gap_write"] for t in turns) / total),
            "read_bytes": read, "read_share": _r(read / (sum(n for _, n in res) or 1)),
            "repeated_share": _r(sum(a["repeated"] for a in ags) / (calls or 1)),
            "turns_per_agent": med([len(a["turns"]) for a in ags])}  # fmt: skip
    if not ags:  # an empty week is no value, never 0%
        out.update({k: None for k in (*METRICS, "cost_share") if k in out})
    return out


def _worse(key: str, prev: Any, now: Any, enough: bool) -> bool | None:
    if not enough or prev is None or now is None:
        return None
    ab, rel = METRICS[key]["tol"]
    return bool(now - prev > max(ab, rel * prev))


QTY = r"(~?)(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*(%|KB|kB|k\b)?"


def _qty(m: re.Match[str]) -> float:
    n = float(m.group(3) or m.group(2))  # a range "A-B" bounds at B
    return {"%": n / 100, "KB": n * 1000, "kB": n * 1000, "k": n * 1000}.get(m.group(4) or "", n)


def parse_prediction(text: str | None) -> tuple[float, float] | None:
    """`predicted` as (baseline, target): "42% -> under 15%", "70.6k -> ~20-25k (...)"."""
    if not text or "->" not in text:
        return None
    before, after = text.split("->", 1)
    a, b = re.search(QTY, before), re.search(QTY, after)
    return (_qty(a), _qty(b)) if a and b else None


def landed_at(
    item: dict[str, Any], repos: Path, seen: dict[str, str], now: dt.datetime
) -> dt.datetime:
    """When the item landed: its `landed_at`, else the commit time of the first sha in its `result`
    that its repo knows, else the first run that saw it done (kept in `seen`)."""
    if item.get("landed_at"):
        return dt.datetime.fromisoformat(str(item["landed_at"]))
    repo = repos / str(item.get("repo", "")).split(" ")[0]
    for sha in re.findall(r"\b[0-9a-f]{7,40}\b", str(item.get("result") or "")):
        r = subprocess.run(["git", "-C", str(repo), "show", "-s", "--format=%cI", f"{sha}^{{commit}}"],
                           capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        if r.returncode == 0 and r.stdout.strip():
            return dt.datetime.fromisoformat(r.stdout.strip())
    seen.setdefault(str(item["id"]), now.isoformat())
    return dt.datetime.fromisoformat(seen[str(item["id"])])


def _metric_key(item: dict[str, Any]) -> str | None:
    text = str(item.get("metric") or "").strip().lower()
    return next((k for k, m in METRICS.items() if text == m["label"].lower()), None)


def check(
    item: dict[str, Any], measured: Any, when: dt.datetime, now: dt.datetime
) -> dict[str, Any]:
    out = {"id": item["id"], "metric": item.get("metric"), "predicted": item.get("predicted"),
           "landed": when.isoformat(), "provisional": now - when < dt.timedelta(days=7)}  # fmt: skip
    pred = parse_prediction(item.get("predicted"))
    if pred is None:
        return out | {"verdict": "not measured", "why": "`predicted` is not 'BASELINE -> TARGET'"}
    if measured is None:
        return out | {
            "verdict": "not measured",
            "why": f"fewer than {MIN_AGENTS} agents since it landed",
        }
    base, target = pred
    sign = 1 if target <= base else -1  # lower is better unless the prediction says otherwise
    verdict = ("confirmed" if sign * measured <= sign * target
               else "short" if sign * measured < sign * base else "regressed")  # fmt: skip
    return out | {"verdict": verdict, "measured": measured, "baseline": base, "target": target}


def calibrate(projects: Path, now: dt.datetime, ccusage: str | None) -> dict[str, Any]:
    """ccusage's token totals over the window's whole UTC days against the reader's, all transcripts."""
    first, last = (
        (now - dt.timedelta(days=7)).date() + dt.timedelta(days=1),
        now.date() - dt.timedelta(days=1),
    )
    if not ccusage:
        return {"status": "unverified", "detail": "no ccusage command"}
    cmd = [*shlex.split(ccusage), "claude", "daily", "--json", "--offline", "-z", "UTC",
           "--since", f"{first:%Y%m%d}", "--until", f"{last:%Y%m%d}"]  # fmt: skip
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False,
                           stdin=subprocess.DEVNULL, env=_ccusage_env(projects))  # fmt: skip
        days = json.loads(r.stdout)["daily"]
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as e:
        return {"status": "unverified", "detail": f"ccusage did not answer: {e}"}
    keys = {"inputTokens": "input", "cacheReadTokens": "read", "cacheCreationTokens": "write"}
    theirs = {v: sum(int(d.get(k, 0)) for d in days) for k, v in keys.items()}
    ours = {"input": 0, "read": 0, "write": 0}
    lo = dt.datetime.combine(first, dt.time(), dt.UTC)
    hi = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(), dt.UTC)
    for path in projects.glob("**/*.jsonl"):
        for t in probe.api_turns(probe._records(path)):
            if lo <= t["at"] < hi:
                ours["input"] += t["input"]
                ours["read"] += t["read"]
                ours["write"] += t["w5"] + t["w1"]
    off = [k for k in ours if abs(ours[k] - theirs[k]) > 0.005 * max(ours[k], theirs[k])]
    detail = f"{first}..{last}: reader {ours}, ccusage {theirs}"
    return {"status": "disagrees" if off else "agrees", "detail": detail}


def _ccusage_env(projects: Path) -> dict[str, str]:
    import os

    return dict(os.environ, CLAUDE_CONFIG_DIR=str(projects.parent))


def _loopctl(loopctl: Path | None, queue: Path | None, *args: str) -> tuple[bool, str]:
    """One loopctl edit OF `queue`, named explicitly: loopctl's own default is the live queue, so an
    unnamed edit from a run that read a scratch queue would land in the owner's (2026-11-08)."""
    if loopctl is None:
        return False, "no loopctl"
    if queue is None:
        return False, "no queue"
    r = subprocess.run([sys.executable, str(loopctl), "--queue", str(queue), *args], capture_output=True, text=True,
                       check=False, timeout=120, stdin=subprocess.DEVNULL)  # fmt: skip
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _file(loopctl: Path | None, queue: Path | None, item: dict[str, Any]) -> dict[str, Any]:
    done, said = _loopctl(loopctl, queue, "file", "--json", json.dumps(item))
    outcome = (
        "filed"
        if done
        else ("already filed" if "already exists" in said else f"not filed: {said[-200:]}")
    )
    return {"id": item["id"], "outcome": outcome}


def _item(iid: str, key: str, text: str, predicted: str) -> dict[str, Any]:
    return {"id": iid, "repo": "gates_of_heck", "phase": "EC", "status": "todo", "depends": [],
            "item": text, "metric": METRICS[key]["label"], "predicted": predicted,
            "owner_words": OWNER_WORDS, "source": "coordinator-found (goh spend watch)",
            "done_state": (f"the cause found and fixed; the spend watch's `{METRICS[key]['label']}` "
                           "back at or under the value named in `predicted` for a full week")}  # fmt: skip


def run(*, projects: Path, now: dt.datetime, queue: Path | None, loopctl: Path | None,
        ccusage: str | None, claude_md: Path, prior: dict[str, Any], repos: Path) -> dict[str, Any]:  # fmt: skip
    ags = agents(projects, now - dt.timedelta(days=14))
    cur = week(ags, now - dt.timedelta(days=7), now)
    prev = week(ags, now - dt.timedelta(days=14), now - dt.timedelta(days=7))
    try:
        cur["claude_md_bytes"] = claude_md.stat().st_size
    except OSError:
        cur["claude_md_bytes"] = None
    prev["claude_md_bytes"] = (prior.get("current") or {}).get("claude_md_bytes")
    enough = cur["agents"] >= MIN_AGENTS and prev["agents"] >= MIN_AGENTS
    delta = {k: {"prev": prev.get(k), "now": cur.get(k),
                 "worse": _worse(k, prev.get(k), cur.get(k), enough or k == "claude_md_bytes")}
             for k in METRICS}  # fmt: skip
    filed = []
    for k, d in delta.items():
        if d["worse"]:
            text = (
                f"Spend watch {now:%Y-%m-%d}: {METRICS[k]['label']} rose from {d['prev']} (week before) "
                f"to {d['now']} (week to {now:%Y-%m-%d}, {cur['agents']} agents). Find what changed."
            )
            filed.append(_file(loopctl, queue, _item(f"SPEND.WORSE.{k.upper()}.{now:%G-W%V}", k, text,
                                              f"{d['now']} -> under {d['prev']}")))  # fmt: skip
    seen = dict(prior.get("first_seen_done") or {})
    items = (
        json.loads(queue.read_text(encoding="utf-8")).get("items", [])
        if queue and queue.exists()
        else []
    )
    preds = []
    for it in items:
        if it.get("status") != "done" or not it.get("predicted"):
            continue
        key = _metric_key(it)
        when = landed_at(it, repos, seen, now)
        if key is None:
            preds.append(check(it, None, when, now) | {"verdict": "not measured",
                         "why": "its `metric` is none the watch computes (METRICS labels)"})  # fmt: skip
            continue
        since = max(when, now - dt.timedelta(days=7))
        w = week(ags, since, now)
        measured = cur["claude_md_bytes"] if key == "claude_md_bytes" else (
            w[key] if w["agents"] >= MIN_AGENTS else None)  # fmt: skip
        c = check(it, measured, when, now)
        preds.append(c)
        if c["provisional"] or c["verdict"] == "not measured":
            continue
        mark = {k: c[k] for k in ("verdict", "measured", "baseline", "target")} | {
            "on": f"{now:%Y-%m-%d}"
        }
        old = it.get("spend_check") or {}
        if (old.get("verdict"), old.get("measured")) == (mark["verdict"], mark["measured"]):
            continue
        _loopctl(loopctl, queue, "set", str(it["id"]), "spend_check", json.dumps(mark), "--json")
        if c["verdict"] == "regressed":
            text = (
                f"Spend watch {now:%Y-%m-%d}: {it['id']} predicted {it['predicted']!r} for "
                f"{it['metric']!r}; measured {c['measured']} since it landed ({when:%Y-%m-%d}), "
                "no better than its baseline. Find why, or revert it."
            )
            filed.append(
                _file(
                    loopctl,
                    queue,
                    _item(f"SPEND.REGRESSED.{it['id']}", key, text, str(it["predicted"])),
                )
            )
    return {"ran_at": now.isoformat(), "current": cur, "previous": prev, "delta": delta,
            "predictions": preds, "filed": filed, "first_seen_done": seen,
            "calibration": calibrate(projects, now, ccusage)}  # fmt: skip


def _fmt(key: str, v: Any) -> str:
    if v is None:
        return "-"
    return f"{v:.1%}" if key.endswith("_share") else f"{v:g}"


def render(spend: dict[str, Any]) -> list[str]:
    cur, prev, cal = spend["current"], spend["previous"], spend["calibration"]
    lines = ["## Spend (subagents, week to the run vs the week before)", "",
             (f"{cur['agents']} agents, {cur['turns']} API turns, {cur['cost_units']} cost units "
             f"(week before: {prev['agents']}, {prev['turns']}, {prev['cost_units']}). "
             f"Reader vs ccusage: {cal['status']} ({cal['detail']})."), "",
             "| metric | week before | this week | delta | verdict |", "|---|---|---|---|---|"]  # fmt: skip
    for k, d in spend["delta"].items():
        both = d["prev"] is not None and d["now"] is not None
        diff = (
            (
                f"{d['now'] - d['prev']:+.1%}"
                if k.endswith("_share")
                else f"{d['now'] - d['prev']:+g}"
            )
            if both
            else "-"
        )
        verdict = {True: "worse", False: "ok", None: "no verdict"}[d["worse"]]
        lines.append(
            f"| {METRICS[k]['label']} | {_fmt(k, d['prev'])} | {_fmt(k, d['now'])} | {diff} | {verdict} |"
        )
    share = lambda w: (
        "/".join(f"{w['cost_share'][k]:.0%}" for k in ("read", "write", "output"))
        if w["cost_share"]
        else "-"
    )
    lines += ["", f"- cost-unit shares read/write/output: {share(prev)} -> {share(cur)}",
              f"- first-turn context by agent type: {cur['first_turn']}",
              f"- post-push context by turn kind: {cur['post_push_by_kind']}",
              f"- Read result bytes: {prev['read_bytes']} -> {cur['read_bytes']}", "",
              "### Landed efficiency items against their prediction", ""]  # fmt: skip
    for p in spend["predictions"]:
        tail = (
            f"measured {p['measured']:g}, predicted {p['predicted']!r}"
            if "measured" in p
            else p.get("why", "")
        )
        lines.append(f"- {p['id']}: {p['verdict']}{' (provisional: under 7 days landed)' if p['provisional'] else ''}"
                     f" -- {tail}")  # fmt: skip
    lines += ["- none" if not spend["predictions"] else "", "### Filed through loopctl", ""]
    lines += [f"- {f['id']}: {f['outcome']}" for f in spend["filed"]] or ["- none"]
    return lines


def fault_candidates(
    rows: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Agents' `Faults seen` (agent_audit.fault_rows) that no queue item's `faults` names verbatim,
    one candidate item per reporting agent, in the shape `loopctl file --json` takes. Listed for the
    coordinator to file or drop: a week's faults run to hundreds, so none is filed unread."""
    carried = {f for i in items for f in i.get("faults") or []}
    out = []
    for r in rows:
        new = [f for f in r.get("faults") or [] if f not in carried]
        if new:
            out.append({"id": f"FAULTS.{str(r.get('agent_id'))[:10]}", "phase": "FX", "status": "todo",
                        "repo": "?", "depends": [], "faults": new,
                        "item": f"Faults agent {r.get('agent_id')} reported at {r.get('time')}: triage each.",
                        "done_state": "each fault fixed red-first, filed as its own item, or closed with the reason"})  # fmt: skip
    return out
