"""The model-choice rules come in three named profiles, pro, max and ultra; the meter narrows them.

Owner 2026-11-15 (GOH.ULTRARULES): the Claude Ultra plan replaced Max, "All four", then "keep
pro/max / ultra profiles. reevaluate the rules on a regular cadence to fit the actual usage". The
active profile is the one the owner DECLARED in `hooks/claude/model_choice.json`
(`profiles.declared`), dropped ONE step along `profiles.order` by the budget guard when token-pool's
Claude 7-day meter is under its floor, older than its age limit, or unreadable. Every refusal names
the active profile and why it is active. `max` is the rules as they stood; `ultra` narrows
pool-first to read-only work and makes opus@medium the cheap first try; `pro` makes sonnet@high the
default write tier.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import model_choice

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
RULES_FILE = GUARD.with_name("model_choice.json")
RULES = json.loads(RULES_FILE.read_text(encoding="utf-8"))

PROFILES = RULES["profiles"]
GUARD_CFG = PROFILES["budget_guard"]
NOW = dt.datetime.now(dt.UTC)


def write_meter(
    pool_dir: Path, left_7d: int | None, age_h: float = 0.1, state: str = "value"
) -> None:
    """token-pool's meter.json as pool/meter.py writes it (schema_version 1)."""
    when = (NOW - dt.timedelta(hours=age_h)).isoformat(timespec="seconds")
    why = (
        ""
        if left_7d is None
        else (
            f"5h: 82% left (resets 13:00 Sun); 7d: {left_7d}% left (resets 12:00 Sat); "
            "read from Claude's usage endpoint at 11:09"
        )
    )
    doc = {"schema_version": 1, "read_at": when, "sources": [
        {"id": "agy", "name": "agy", "state": "value", "short": "x", "why": "7d: 99% left",
         "read_at": when},
        {"id": GUARD_CFG["source"], "name": "Claude", "state": state, "short": "", "why": why,
         "read_at": when}]}  # fmt: skip
    pool_dir.mkdir(parents=True, exist_ok=True)
    (pool_dir / GUARD_CFG["meter_file"]).write_text(json.dumps(doc))


def rules_file(tmp: Path, declared: str) -> Path:
    data = json.loads(json.dumps(RULES))
    data["profiles"]["declared"] = declared
    path = tmp / f"rules-{declared}.json"
    path.write_text(json.dumps(data))
    return path


def guard(tmp: Path, model: str, effort: str, because: str, *, pool: bool = False,
          declared: str = "ultra", left: int | None = 95, age_h: float = 0.1,
          meter: bool = True) -> dict[str, Any] | None:  # fmt: skip
    pool_dir = tmp / "pool"
    if meter:
        write_meter(pool_dir, left, age_h)
    prompt = f"Fix the leak.\nModel-choice: {model}@{effort} because {because}"
    if pool:
        prompt += "\nPool: unfit because it edits this repo"
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
             "tool_use_id": "t", "tool_input": {"description": "d", "prompt": prompt,
                                                "model": model, "effort": effort,
                                                "subagent_type": "repo-worker"}}  # fmt: skip
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        env=hermetic_env(XDG_CACHE_HOME=str(tmp / "cache"), POOL_STATE_DIR=str(pool_dir),
                         GOH_MODEL_CHOICE=str(rules_file(tmp, declared))),
        timeout=30, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def denied(out: dict[str, Any] | None) -> str | None:
    hso = (out or {}).get("hookSpecificOutput") or {}
    return hso.get("permissionDecisionReason") if hso.get("permissionDecision") == "deny" else None


def ledger(tmp: Path) -> list[dict[str, Any]]:
    path = tmp / "cache" / "goh" / "agent-spawns.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# --- the file ----------------------------------------------------------------------------------


def test_the_three_profiles_are_named_and_the_owner_declared_ultra() -> None:
    assert PROFILES["order"] == ["ultra", "max", "pro"]
    assert PROFILES["declared"] == "ultra"
    for name in PROFILES["order"]:
        assert PROFILES[name]["about"], name
    assert PROFILES["max"]["changes"] == {}, "max is today's rules exactly"
    assert "All four" in PROFILES["decided"] and "regular cadence" in PROFILES["decided"]
    assert PROFILES["measured"]["spawns"] == 615 and PROFILES["measured"]["pool_unfit"] == 600


def test_max_is_the_top_level_rules_exactly() -> None:
    r = model_choice.Rules(RULES, RULES_FILE, profile="max")
    assert r.tiers == RULES["tiers"] and r.pool_first == RULES["pool_first"]
    assert r.exploration == RULES["exploration"]


@pytest.mark.parametrize("name", ["pro", "max", "ultra"])
def test_every_profile_rests_on_facts_and_never_routes_to_a_never_tier(name: str) -> None:
    r = model_choice.Rules(RULES, RULES_FILE, profile=name)
    for key, tier in r.tiers.items():
        assert set(tier["rests_on"]) <= set(RULES["facts"]), (name, key)
        if tier["tier"] == "never":
            assert tier["why"], (name, key)
    alt = r.exploration["cheap_alternation"]
    if alt["enabled"]:
        assert all(r.rule(*c.split("@"))["tier"] != "never" for c in alt["between"]), name


def test_ultra_is_max_plus_the_four_changes() -> None:
    u = model_choice.Rules(RULES, RULES_FILE, profile="ultra")
    assert u.pool_first["tiers"] == ["lookup"], "pool-first narrows to read-only work"
    med = u.tiers["opus@medium"]
    assert med["tier"] == "cheap" and med["also_all"] == ["checkable", "known-fix"]
    assert set(RULES["tiers"]["opus@medium"]["tags"]) <= set(med["tags"]), "keeps its own tags"
    for cls in ("sonnet@high", "opus@low"):
        assert (
            u.tiers[cls]["tier"] == "never" and "price was its only reason" in u.tiers[cls]["why"]
        )
    assert u.tiers["opus@high"] == RULES["tiers"]["opus@high"], "the one escalation stays"


def test_a_spawn_profile_route_never_lands_on_a_never_tier() -> None:
    """Exploration on, under a profile that made its targets `never`: the router declines."""
    data = json.loads(json.dumps(RULES))
    data["profiles"]["ultra"]["changes"]["exploration"] = {}
    u = model_choice.Rules(data, RULES_FILE, profile="ultra")
    for sha in ("0" * 64, "1" * 64, "f" * 64):
        got = u.route("opus@medium", ["checkable", "known-fix"], sha)
        assert got is None or u.rule(*got[0].split("@"))["tier"] != "never", got


# --- the guard ---------------------------------------------------------------------------------


def test_opus_medium_checkable_known_fix_with_no_pool_line_passes_under_ultra(
    tmp_path: Path,
) -> None:
    out = guard(tmp_path, "opus", "medium", "checkable, known-fix")
    assert denied(out) is None, denied(out)
    (line,) = ledger(tmp_path)
    assert line["profile"] == "ultra" and line["pool"] is None
    assert line["meter"]["7d"] == 95


def test_a_lookup_still_weighs_the_pool_under_ultra(tmp_path: Path) -> None:
    why = denied(guard(tmp_path, "haiku", "low", "read-only"))
    assert why and "pool" in why.lower() and "ultra" in why


def test_sonnet_high_is_refused_under_ultra_and_allowed_under_max(tmp_path: Path) -> None:
    why = denied(guard(tmp_path / "u", "sonnet", "high", "checkable, known-fix", pool=True))
    assert why and "never" in why and "profile ultra" in why, why
    out = guard(tmp_path / "m", "sonnet", "high", "checkable, known-fix", pool=True,
                declared="max")  # fmt: skip
    assert denied(out) is None, denied(out)


def test_a_stale_meter_drops_ultra_to_max(tmp_path: Path) -> None:
    age = GUARD_CFG["max_age_h"] + 1
    out = guard(tmp_path, "sonnet", "high", "checkable, known-fix", pool=True, age_h=age)
    assert denied(out) is None, "max allows the cheap sonnet tier"
    assert ledger(tmp_path)[0]["profile"] == "max"
    why = denied(guard(tmp_path / "b", "opus", "medium", "checkable, known-fix", age_h=age))
    assert why and "profile max" in why and "declared ultra" in why and "old" in why, why


def test_a_low_meter_drops_ultra_to_max(tmp_path: Path) -> None:
    low = GUARD_CFG["min_left_pct"] - 1
    why = denied(guard(tmp_path, "opus", "medium", "checkable, known-fix", left=low))
    assert why and "profile max" in why and f"{low}% left" in why, why


def test_an_unreadable_meter_drops_ultra_to_max(tmp_path: Path) -> None:
    why = denied(guard(tmp_path, "opus", "medium", "checkable, known-fix", meter=False))
    assert why and "profile max" in why and "could not be read" in why, why
    why = denied(guard(tmp_path / "n", "opus", "medium", "checkable, known-fix", left=None))
    assert why and "profile max" in why, why


def test_the_guard_drops_one_profile_only_and_pro_is_the_floor(tmp_path: Path) -> None:
    out = guard(tmp_path, "sonnet", "high", "cross-module", pool=True, declared="max", left=5)
    assert denied(out) is None and ledger(tmp_path)[0]["profile"] == "pro"
    out = guard(tmp_path / "p", "sonnet", "high", "cross-module", pool=True, declared="pro", left=5)
    assert denied(out) is None and ledger(tmp_path / "p")[0]["profile"] == "pro"


def test_pro_writes_on_sonnet_and_keeps_opus_for_the_hard_tags(tmp_path: Path) -> None:
    def pro(*a: str, **k: Any) -> str | None:
        return denied(guard(tmp_path / str(len(list(tmp_path.iterdir()))), *a, pool=True,
                            declared="pro", **k))  # fmt: skip

    assert pro("sonnet", "high", "cross-module") is None
    assert pro("opus", "high", "unknown-root-cause") is None
    why = pro("opus", "high", "cross-module")
    assert why and "profile pro" in why
    why = pro("opus", "low", "checkable, known-fix")
    assert why and "never" in why
    why = pro("sonnet", "high", "checkable", meter=True, left=95)
    assert why is None
    why = denied(guard(tmp_path / "np", "sonnet", "high", "checkable", declared="pro"))
    assert why and "pool" in why.lower(), "a pool line on every tier"


def test_every_refusal_names_the_active_profile_and_why(tmp_path: Path) -> None:
    for i, (m, e, b) in enumerate([("opus", "low", "checkable"), ("fable", "low", "x-unknown"),
                                   ("haiku", "low", "read-only")]):  # fmt: skip
        why = denied(guard(tmp_path / str(i), m, e, b))
        assert why and "profile ultra" in why and "declared" in why and "95% left" in why, why
