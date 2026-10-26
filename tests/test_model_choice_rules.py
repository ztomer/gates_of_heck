"""The subagent model-choice rules live in ONE file, with the facts they rest on, and are read from it.

The owner (2026-10-18): "continually improve this approach, otherwise we might get stuck with
outdated rules". A rule is only as current as the prices, default efforts, aliases and Claude Code
version it was measured on, so `hooks/claude/model_choice.json` carries each of those with the date
it was measured and its source, and every tier names the facts it rests on -- the weekly watch
(`scripts/model_choice_watch.py`) re-checks the facts and marks what rests on a changed one. The
hook and the audit read the file; a second spelling of the table anywhere is a second table that
drifts, so they hold no tier, price or version literal of their own.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from conftest import REPO_ROOT, hermetic_env

RULES_FILE = REPO_ROOT / "hooks" / "claude" / "model_choice.json"
RULES = json.loads(RULES_FILE.read_text(encoding="utf-8"))
GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
READERS = (GUARD, REPO_ROOT / "scripts" / "agent_audit.py")
CHECK_KINDS = {"claude_version", "agent_enum", "contains", "price_row"}

CLASS_LITERAL = re.compile(r"\b(?:sonnet|opus|haiku|fable)@(?:low|medium|high|xhigh|max)\b|\*@max")
PRICE_LITERAL = re.compile(r"\$\d")
VERSION_LITERAL = re.compile(r"\b2\.1\.\d{2,}\b")


def test_the_readers_restate_no_tier_price_or_version() -> None:
    for path in READERS:
        text = path.read_text(encoding="utf-8")
        for pat in (CLASS_LITERAL, PRICE_LITERAL, VERSION_LITERAL):
            hits = sorted(set(pat.findall(text)))
            assert not hits, f"{path.name} restates the rules file: {hits}"


def test_every_fact_carries_its_value_date_source_and_check() -> None:
    for name, fact in RULES["facts"].items():
        assert "value" in fact, name
        dt.date.fromisoformat(fact["measured"])
        assert fact["source"], name
        assert fact["check"]["kind"] in CHECK_KINDS, name
        if fact["check"]["kind"] in {"contains", "price_row"}:
            assert fact["check"]["url"].startswith("https://"), name


def test_the_facts_the_spec_names_are_all_there() -> None:
    facts = RULES["facts"]
    for fam in facts["agent.models"]["value"]:
        assert f"price.{fam}" in facts, f"no price for the alias {fam}"
    assert set(facts["claude_code.default_effort"]["value"]) >= {"opus", "sonnet", "haiku"}
    assert facts["claude_code.version"]["value"] == RULES["proven_on"]["claude_code"]


def test_every_tier_rests_on_facts_that_exist() -> None:
    for key, tier in RULES["tiers"].items():
        assert tier["rests_on"], key
        assert set(tier["rests_on"]) <= set(RULES["facts"]), key
        if tier["tier"] == "never":
            assert tier["why"], key
        else:
            assert tier["need"] in {"all", "any"} and tier["tags"], key


def test_opus_to_cheap_exploration_is_off_until_the_owner_turns_it_on() -> None:
    down = RULES["exploration"]["opus_to_cheap"]
    assert down["enabled"] is False
    assert "OWNER DECISION" in down["decided"]
    assert "checkable" in down["requires_tags"]


def _guard(event: dict[str, Any], cache: Path, rules: Path) -> dict[str, Any] | None:
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        env=hermetic_env(XDG_CACHE_HOME=str(cache), GOH_MODEL_CHOICE=str(rules)), timeout=30,
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def _event(model: str, effort: str, decl: str) -> dict[str, Any]:
    prompt = f"Fix it.\nModel-choice: {decl}\nPool: unfit because it needs the repo's tools"
    return {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
            "tool_use_id": "t", "tool_input": {"description": "d", "prompt": prompt,
                                               "model": model, "effort": effort}}  # fmt: skip


def test_the_hook_judges_by_the_rules_file_it_is_given(tmp_path: Path) -> None:
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    event = _event("sonnet", "medium", "sonnet@medium because checkable")
    out = _guard(event, tmp_path, RULES_FILE)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    data["tiers"]["sonnet@medium"] = {"tier": "cheap", "need": "all", "tags": ["checkable"],
                                      "rests_on": ["price.sonnet"]}  # fmt: skip
    changed = tmp_path / "rules.json"
    changed.write_text(json.dumps(data))
    out = _guard(event, tmp_path, changed)
    assert out is not None and "permissionDecision" not in out["hookSpecificOutput"], out


def test_the_hook_fails_open_when_the_rules_file_cannot_be_read(tmp_path: Path) -> None:
    event = _event("", "", "sonnet@high because checkable, known-fix")  # names no model
    out = _guard(event, tmp_path, tmp_path / "missing.json")
    assert out is not None
    assert "permissionDecision" not in out.get("hookSpecificOutput", {}), out
    assert "rules" in out["systemMessage"]


def test_pool_first_names_real_tiers_and_a_task_class_for_each() -> None:
    """Owner ruling 2026-10-25: cheap and lookup work goes to token-pool's `ask` before a Claude
    subagent. The rules file says which tiers that is and which pool class each maps to."""
    pool = RULES["pool_first"]
    tiers = {v["tier"] for v in RULES["tiers"].values()}
    assert pool["tiers"] and set(pool["tiers"]) <= tiers - {"never"}
    assert set(pool["task_class"]) == set(pool["tiers"])
    assert pool["tool"].startswith("mcp__") and pool["decided"]
