"""usage_review.py -- the weekly usage review: do the model-choice rules fit the week's usage?

Owner 2026-11-15 (GOH.ULTRARULES): "keep pro/max / ultra profiles. reevaluate the rules on a regular
cadence to fit the actual usage". `scripts/model_choice_watch.py` (weekly) runs this over:

* the spawn ledger: spawns per class, `Pool: tried` against `unfit`, the redo rate (blocked or
  failed) and the escalation rate, and the API-equivalent spend per class (tokens x `facts.price`);
* the Claude meter: 5h and 7d left now (token-pool's meter, the budget guard's reader) and the
  week's minimum (agent_guard keeps the reading on every spawn line);
* the machine: the load average when an agent that landed finished (agent_guard's outcome line).

It PROPOSES -- a profile to declare, and the values that would change -- by the thresholds in the
rules file's `profiles.review`; the owner applies. Nothing here writes the rules file.
Pinned by tests/test_usage_review.py.

the weekly usage review `model_choice_watch.py` runs: the week's spawns, pool lines, redo,
escalation and spend per class, the Claude meter's minimum and the load when agents landed propose
a profile and the values it changes (a macOS notice); it never edits the rules file. Pinned by
`test_usage_review.py`.

`scripts/model_choice_watch.py`: the weekly no-token job: re-checks every rules-file fact against
its source (pricing/model-config pages, the binary's Agent enums, `claude --version`) and marks a
changed one and its tiers `re-research`; on a new Claude Code version re-runs the hook's live proof
once (one capped haiku `claude -p`); a canary goes red when spawns stopped reaching the ledger or
settings.json's goh hooks differ from `registrations.json`; runs the audit and the SPEND section
(`scripts/spend_watch.py`). Writes `~/.cache/goh/model-choice-findings.md`, and its record and
fault candidates to goh.db (`kv` `model-choice-watch`, `fault_candidates`; `hook_ledgers.py`),
prints the spend table and one summary line. `--install` loads the weekly launchd job. Pinned by
`test_model_choice_watch.py`.

`scripts/spend_watch.py`: the weekly watch's SPEND section (GOH.SPENDWATCH): per week, from the
subagent transcripts (read by `claude_cache_probe.api_turns`, one usage per `message.id`),
cost-unit shares, first-turn context per agent type and of write agents, context share from the
first push/release on by turn kind, idle-gap cache writes, Read bytes and share of text results,
image results by tool and image tokens per agent (`scripts/image_cost.py`: width * height / 750
from the image header, never its bytes; GOH.IMGREAD), repeated calls, turns per agent, CLAUDE.md
bytes; deltas against the week before. ccusage (`npx -y ccusage@20.0.48`, MIT) calibrates the
reader each run (token totals over the window's whole UTC days must agree). A worse week, and a
landed `EC` item whose `metric` is a `METRICS` label and whose measurement is no better than its
`predicted` baseline, are filed with `loopctl --queue <the queue it read> file`; the verdict
(confirmed/short/regressed) is set as the item's `spend_check` 7 days after it landed. Agents'
unfiled `Faults seen` become candidate items (listed, not filed). Pinned by `test_spend_watch.py`.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks" / "claude"))
import model_choice

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location("goh_gate_slot", ROOT / "lib" / "gate_slot.py")
assert _spec and _spec.loader
gate_slot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate_slot)  # the machine's gate-slot default, read where it is defined

REDO = {"blocked", "failed"}  # agent_audit.NOT_LANDED: the work was not done


def _when(stamp: Any) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def _week(records: list[dict[str, Any]], now: dt.datetime, days: int) -> list[dict[str, Any]]:
    since = now - dt.timedelta(days=days)
    return [r for r in records if (w := _when(r.get("time"))) is not None and since <= w <= now]


def ledger_stats(records: list[dict[str, Any]], rules: model_choice.Rules) -> dict[str, Any]:
    """Per-class counts, pool lines, redo and escalation rates and spend over one week's records."""
    outcomes = {
        (r.get("session_id"), r.get("tool_use_id")): r
        for r in records
        if r.get("event") == "outcome"
    }
    spawns = [r for r in records if r.get("event") == "spawn" and not r.get("exempt")]
    per: dict[str, dict[str, Any]] = {}
    pool = {"tried": 0, "unfit": 0, "none": 0}
    for s in spawns:
        cls = s.get("declared") or f"{s.get('model')}@{s.get('effort')}"
        row = per.setdefault(
            cls, {"spawns": 0, "outcomes": 0, "redo": 0, "escalation": 0, "usd": 0.0}
        )
        row["spawns"] += 1
        said = (s.get("pool") or {}).get("said") if isinstance(s.get("pool"), dict) else None
        pool[said if said in pool else "none"] += 1
        row["escalation"] += "escalation" in (s.get("tags") or [])
        out = outcomes.get((s.get("session_id"), s.get("tool_use_id")))
        if out is None:
            continue
        row["outcomes"] += 1
        row["redo"] += out.get("status") in REDO
        row["usd"] += rules.cost(s.get("model"), out.get("tokens")) or 0.0
    total_usd = sum(r["usd"] for r in per.values())
    for row in per.values():
        row["redo_rate"] = round(row["redo"] / row["outcomes"], 3) if row["outcomes"] else None
        row["usd"] = round(row["usd"], 2)
        row["spend_share"] = round(row["usd"] / total_usd, 3) if total_usd else None
    n = len(spawns)
    return {
        "spawns": n,
        "per_class": dict(sorted(per.items())),
        "pool": pool,
        "pool_unfit_share": round(pool["unfit"] / n, 3) if n else None,
        "escalation_rate": round(sum(r["escalation"] for r in per.values()) / n, 3) if n else None,
        "usd": round(total_usd, 2),
    }


def meter_stats(
    records: list[dict[str, Any]], now_reading: dict[str, Any] | None, windows: list[str]
) -> dict[str, Any]:
    """The meter now and the week's minimum per window (spawn lines' readings and now's)."""
    readings = [
        r["meter"]
        for r in records
        if r.get("event") == "spawn" and isinstance(r.get("meter"), dict)
    ]
    if now_reading:
        readings.append(now_reading)
    out: dict[str, Any] = {
        "now": {w: (now_reading or {}).get(w) for w in windows},
        "now_why": (now_reading or {}).get("why"),
        "readings": len(readings),
    }
    for w in windows:
        vals = [x[w] for x in readings if isinstance(x.get(w), int)]
        out[f"min_{w}"] = min(vals) if vals else None
    return out


def load_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The 1-minute load average when an agent that landed finished."""
    landed = [
        r
        for r in records
        if r.get("event") == "outcome"
        and r.get("status") == "landed"
        and isinstance(r.get("load"), list)
        and r["load"]
    ]
    ones = [float(r["load"][0]) for r in landed]
    cores = next((r.get("cores") for r in landed if r.get("cores")), None)
    return {
        "lands": len(ones),
        "max": max(ones) if ones else None,
        "median": round(statistics.median(ones), 2) if ones else None,
        "cores": cores,
    }


def changes(data: dict[str, Any], path: Path, frm: str, to: str) -> list[str]:
    """What moving `profiles.declared` from `frm` to `to` would change, one line per value."""
    a = model_choice.Rules(data, path, profile=frm)
    b = model_choice.Rules(data, path, profile=to)
    out = []
    if a.pool_first.get("tiers") != b.pool_first.get("tiers"):
        out.append(f"pool_first.tiers: {a.pool_first.get('tiers')} -> {b.pool_first.get('tiers')}")
    for key in sorted(set(a.tiers) | set(b.tiers)):
        x, y = a.tiers.get(key), b.tiers.get(key)
        if x == y:
            continue
        diff = {
            k: (x.get(k) if x else None, y.get(k) if y else None)
            for k in ("tier", "need", "tags", "also_all")
            if (x or {}).get(k) != (y or {}).get(k)
        }
        parts = "; ".join(f"{k} {p} -> {q}" for k, (p, q) in diff.items()) or "rests_on/why only"
        out.append(f"tiers.{key}: {parts}")
    ea = (a.exploration.get("cheap_alternation") or {}).get("enabled")
    eb = (b.exploration.get("cheap_alternation") or {}).get("enabled")
    if ea != eb:
        out.append(f"exploration.cheap_alternation.enabled: {ea} -> {eb}")
    return out


def propose(
    data: dict[str, Any],
    path: Path,
    ledger: dict[str, Any],
    meter: dict[str, Any],
    load: dict[str, Any],
) -> list[dict[str, Any]]:
    """The proposals the week's numbers make, each with its evidence and the values it changes."""
    prof = data["profiles"]
    guard, rev, order, declared = (
        prof["budget_guard"],
        prof["review"],
        prof["order"],
        prof["declared"],
    )
    w = guard["window"]
    low = meter.get(f"min_{w}")
    i = order.index(declared)
    out: list[dict[str, Any]] = []
    if low is None:
        pass  # no reading this week: nothing to judge the plan by
    elif low < guard["min_left_pct"] and i + 1 < len(order):
        to = order[i + 1]
        out.append(
            {
                "kind": "profile",
                "from": declared,
                "to": to,
                "why": (
                    f"the week's lowest {w} reading was {low}% left, under the guard's "
                    f"{guard['min_left_pct']}%: the declared plan's headroom was not there"
                ),
                "changes": changes(data, path, declared, to),
            }
        )
    elif low >= rev["up_min_left_pct"] and i > 0:
        to = order[i - 1]
        unfit, bar = ledger.get("pool_unfit_share"), rev["pool_unfit_share"]
        evidence = ""
        if unfit is not None and unfit >= bar:
            evidence = (
                f"; {unfit:.0%} of {ledger['spawns']} spawns wrote `Pool: unfit` "
                f"(the line is ceremony on write work at {bar:.0%} or more)"
            )
        out.append(
            {
                "kind": "profile",
                "from": declared,
                "to": to,
                "why": (
                    f"the week's lowest {w} reading was {low}% left (at least "
                    f"{rev['up_min_left_pct']}%): price was not the limit{evidence}"
                ),
                "changes": changes(data, path, declared, to),
            }
        )
    cores, peak = load.get("cores"), load.get("max")
    if cores and peak is not None and peak > rev["load_per_core"] * cores:
        now_slots = gate_slot.default_slots()
        out.append(
            {
                "kind": "machine",
                "from": now_slots,
                "to": max(1, now_slots // 2),
                "why": (
                    f"load average {peak} when an agent landed ({load['lands']} lands, "
                    f"median {load['median']}) on {cores} cores, over "
                    f"{rev['load_per_core']} per core"
                ),
                "changes": [
                    (
                        f"GOH_GATE_SLOTS: {now_slots} -> {max(1, now_slots // 2)} "
                        "(machine config: lib/gate_slot.py default_slots, or the "
                        "environment)"
                    )
                ],
            }
        )
    return out


def review(
    records: list[dict[str, Any]],
    data: dict[str, Any],
    path: Path,
    now: dt.datetime,
    now_reading: dict[str, Any] | None,
    days: int = 7,
) -> dict[str, Any]:
    """The week's numbers and the proposals they make."""
    week = _week(records, now, days)
    rules = model_choice.Rules(data, path, profile=data["profiles"]["declared"])
    ledger = ledger_stats(week, rules)
    meter = meter_stats(week, now_reading, data["profiles"]["budget_guard"]["windows"])
    load = load_stats(week)
    return {
        "since": (now - dt.timedelta(days=days)).isoformat(timespec="seconds"),
        "declared": data["profiles"]["declared"],
        "ledger": ledger,
        "meter": meter,
        "load": load,
        "proposals": propose(data, path, ledger, meter, load),
    }


def render(r: dict[str, Any]) -> list[str]:
    led, met, load = r["ledger"], r["meter"], r["load"]
    lines = [
        "## Usage review (weekly; proposals for the owner, who applies them)",
        "",
        f"Since {r['since']}, declared profile **{r['declared']}**.",
        "",
        (
            f"- spawns {led['spawns']}: pool tried {led['pool']['tried']}, unfit "
            f"{led['pool']['unfit']}, no line {led['pool']['none']}; escalation rate "
            f"{led['escalation_rate']}; API-equivalent ${led['usd']}"
        ),
        (
            f"- meter now {met['now']} ({met['now_why']}); the week's minimum "
            + ", ".join(f"{k[4:]} {v}" for k, v in met.items() if k.startswith("min_"))
            + f" over {met['readings']} readings"
        ),
        (
            f"- load when an agent landed: max {load['max']}, median {load['median']} over "
            f"{load['lands']} lands on {load['cores']} cores"
        ),
        "",
        "| class | spawns | outcomes | redo rate | escalations | USD | share |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {c} | {x['spawns']} | {x['outcomes']} | {x['redo_rate']} | {x['escalation']} | "
        f"{x['usd']} | {x['spend_share']} |"
        for c, x in led["per_class"].items()
    ]
    lines += ["", "### Proposed", ""]
    for p in r["proposals"]:
        lines.append(f"- {p['kind']}: {p['from']} -> {p['to']} -- {p['why']}")
        lines += [f"  - {c}" for c in p["changes"]]
    if not r["proposals"]:
        lines.append("- none: the week's usage fits the declared profile")
    return lines


def notice(r: dict[str, Any]) -> str | None:
    """The one-line macOS notice for a review that proposes something, else None."""
    if not r["proposals"]:
        return None
    return "; ".join(f"{p['kind']} {p['from']} -> {p['to']}" for p in r["proposals"])


def run(
    records: list[dict[str, Any]], rules: model_choice.Rules, days: int
) -> dict[str, Any] | None:
    """The review over `records` now, with the meter read now (None: the rules name no profiles)."""
    if not rules.data.get("profiles"):
        return None
    reading = model_choice.read_meter(rules.data["profiles"]["budget_guard"])
    now = dt.datetime.now(dt.UTC)
    return review(records, rules.data, rules.path, now, reading, days)


def post(r: dict[str, Any] | None, how: str, findings: Path) -> None:
    """The macOS notice for a review that proposes something: agent_stalls' osascript notification
    (the house's one), a command given the title and the text (tests; another notifier), or none."""
    said = notice(r) if r else None
    if not said or not how:
        return
    title, text = "goh usage review: a model-choice proposal", f"{said} -- {findings}"
    if how == "osascript":
        importlib.import_module("agent_stalls").osascript_notify(title, text)
        return
    subprocess.run([how, title, text], check=False, capture_output=True, timeout=60,
                   stdin=subprocess.DEVNULL)  # fmt: skip
