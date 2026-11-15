"""The weekly usage review proposes the profile the week's usage fits, and never edits the rules.

Owner 2026-11-15 (GOH.ULTRARULES): "reevaluate the rules on a regular cadence to fit the actual
usage". `scripts/usage_review.py`, run by the weekly `scripts/model_choice_watch.py`, reads the
spawn ledger, the Claude meter and the load when agents landed, and writes a proposal -- the
profile, and the values it would change -- to the findings, with a macOS notice. CALIBRATED both
ways: the week the owner ruled on (615 spawns, 600 `Pool: unfit`, 7d 95% left, load 118-139 on 16
cores), replayed under the max rules it ran on, proposes exactly the ultra changes; a week of low
meter readings under ultra proposes the opposite.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import usage_review

RULES_FILE = REPO_ROOT / "hooks" / "claude" / "model_choice.json"
RULES = json.loads(RULES_FILE.read_text(encoding="utf-8"))
WATCH = REPO_ROOT / "scripts" / "model_choice_watch.py"
NOW = dt.datetime(2026, 11, 15, 16, 30, tzinfo=dt.UTC)
TOKENS = {"input": 10_000, "output": 20_000, "cache_creation": 50_000, "cache_read": 900_000}


def declared(name: str) -> dict[str, Any]:
    data = json.loads(json.dumps(RULES))
    data["profiles"]["declared"] = name
    return data


def the_week(left_7d: int, load: float = 139.0) -> list[dict[str, Any]]:
    """The measured week's shape: 615 spawns, 600 `unfit`, 13 `tried`, 2 with no line; most land."""
    classes = ["opus@medium"] * 250 + ["sonnet@high"] * 230 + ["opus@high"] * 107 + \
              ["haiku@low"] * 20 + ["opus@low"] * 8  # fmt: skip
    said = ["unfit"] * 600 + ["tried"] * 13 + [None] * 2
    rows = []
    for i, (cls, pool) in enumerate(zip(classes, said, strict=True)):
        model, effort = cls.split("@")
        when = (NOW - dt.timedelta(minutes=15 * (615 - i))).isoformat(timespec="seconds")
        rows.append({"event": "spawn", "time": when, "session_id": "s", "tool_use_id": f"t{i}",
                     "model": model, "effort": effort, "declared": cls, "exempt": False,
                     "tags": ["escalation"] if i % 50 == 0 else ["checkable"],
                     "pool": {"said": pool, "why": "w"} if pool else None,
                     "meter": {"5h": 82, "7d": left_7d, "ok": True}})  # fmt: skip
        rows.append({"event": "outcome", "time": when, "session_id": "s", "tool_use_id": f"t{i}",
                     "status": "blocked" if i % 25 == 0 else "landed", "tokens": TOKENS,
                     "load": [load - (i % 22), 100.0, 90.0], "cores": 16})  # fmt: skip
    return rows


def test_this_week_replayed_on_max_proposes_the_ultra_changes() -> None:
    r = usage_review.review(the_week(95), declared("max"), RULES_FILE, NOW, None)
    assert r["ledger"]["spawns"] == 615
    assert r["ledger"]["pool"] == {"tried": 13, "unfit": 600, "none": 2}
    assert r["meter"]["min_7d"] == 95 and r["load"]["max"] == 139.0
    (prof,) = [p for p in r["proposals"] if p["kind"] == "profile"]
    assert (prof["from"], prof["to"]) == ("max", "ultra")
    assert "Pool: unfit" in prof["why"]
    got = "\n".join(prof["changes"])
    assert "pool_first.tiers:" in got and "-> ['lookup']" in got
    assert "tiers.sonnet@high: tier cheap -> never" in got
    assert "tiers.opus@low: tier cheap -> never" in got
    assert "tiers.opus@medium: tier opus -> cheap" in got
    assert "also_all None -> ['checkable', 'known-fix']" in got
    assert "exploration.cheap_alternation.enabled: True -> False" in got
    assert "opus@high" not in got, "the one escalation stays"
    (machine,) = [p for p in r["proposals"] if p["kind"] == "machine"]
    assert "GOH_GATE_SLOTS" in machine["changes"][0]


def test_a_week_of_low_meter_readings_proposes_the_opposite() -> None:
    low = RULES["profiles"]["budget_guard"]["min_left_pct"] - 10
    r = usage_review.review(the_week(low, load=20.0), declared("ultra"), RULES_FILE, NOW, None)
    (prof,) = r["proposals"]
    assert (prof["kind"], prof["from"], prof["to"]) == ("profile", "ultra", "max")
    got = "\n".join(prof["changes"])
    assert "tiers.sonnet@high: tier never -> cheap" in got
    assert "pool_first.tiers: ['lookup'] ->" in got


def test_a_week_that_fits_the_declared_profile_proposes_nothing() -> None:
    r = usage_review.review(the_week(45, load=20.0), declared("ultra"), RULES_FILE, NOW, None)
    assert r["proposals"] == []
    r = usage_review.review([], declared("ultra"), RULES_FILE, NOW, None)
    assert r["proposals"] == [] and r["meter"]["min_7d"] is None


def test_the_reading_now_counts_toward_the_weeks_minimum() -> None:
    now = {"5h": 3, "7d": 12, "ok": False, "why": "low"}
    r = usage_review.review(the_week(95, load=20.0), declared("ultra"), RULES_FILE, NOW, now)
    assert r["meter"]["min_7d"] == 12 and r["meter"]["min_5h"] == 3
    assert r["proposals"][0]["to"] == "max"


def test_the_watch_writes_a_proposal_posts_a_notice_and_leaves_the_rules(tmp_path: Path) -> None:
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(declared("max")))
    before = rules.read_bytes()
    now = dt.datetime.now(dt.UTC)
    week = the_week(95)
    shift = now - NOW
    for row in week:
        row["time"] = (dt.datetime.fromisoformat(row["time"]) + shift).isoformat()
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(x) + "\n" for x in week))
    said = tmp_path / "notice.txt"
    notify = tmp_path / "notify"
    notify.write_text(f'#!/bin/sh\nprintf \'%s|%s\' "$1" "$2" > {said}\n')
    notify.chmod(0o755)
    r = subprocess.run(
        [sys.executable, str(WATCH), "--claude", "", "--ledger", str(ledger), "--projects",
         str(tmp_path / "projects"), "--findings", str(tmp_path / "findings.md"), "--state",
         str(tmp_path / "state.json"), "--root", str(tmp_path), "--offline-audit", "--no-spend",
         "--no-live-proof", "--settings", str(tmp_path / "settings.json"),
         "--notify", str(notify)],
        capture_output=True, text=True, timeout=patience(120), stdin=subprocess.DEVNULL,
        check=False,
        env=hermetic_env(NO_COLOR="1", GOH_MODEL_CHOICE=str(rules),
                         XDG_CACHE_HOME=str(tmp_path / "cache"),
                         POOL_STATE_DIR=str(tmp_path / "no-pool")),
    )  # fmt: skip
    assert "Traceback" not in r.stderr, r.stderr
    findings = (tmp_path / "findings.md").read_text()
    assert "## Usage review" in findings and "profile: max -> ultra" in findings
    assert "tiers.sonnet@high: tier cheap -> never" in findings
    assert said.read_text().split("|")[1].startswith("profile max -> ultra"), said.read_text()
    assert rules.read_bytes() == before, "the review proposes; it never edits the rules file"
    state = json.loads((tmp_path / "state.json").read_text())
    assert state["usage_review"]["proposals"][0]["to"] == "ultra"
