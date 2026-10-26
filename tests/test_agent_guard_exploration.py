"""Bounded exploration: the hook routes some spawns to the other tier, so the ledger can compare them.

A ledger that only ever sees the tier each caller chose cannot say whether the choice was right:
every Sonnet@high outcome is a Sonnet@high outcome on work somebody judged Sonnet-shaped. So
(owner, 2026-10-18, goh model-choice loop item 3):

* a spawn declared on a cheap tier (checkable + known fix) runs on whichever of the two cheap tiers
  the parity of its prompt's sha picks -- deterministic, so a respawn cannot shop for a tier -- and
  its ledger line says `explored: true` with what was declared;
* a fixed fraction of opus@medium spawns that declare `checkable` (and not `unchecked`) may run on
  the cheap tier instead. That direction is OFF until the owner turns it on in the rules file.

Whatever the rules file says, a spawn without the mechanical check is never routed down.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
RULES_FILE = GUARD.with_name("model_choice.json")
POOL = "Pool: unfit because the fix needs this repo's files and its test run"


def _run(tool_input: dict[str, Any], cache: Path, rules: Path = RULES_FILE) -> dict[str, Any]:
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Agent",
             "tool_use_id": "t", "tool_input": tool_input}  # fmt: skip
    r = subprocess.run(
        [sys.executable, str(GUARD)], input=json.dumps(event), capture_output=True, text=True,
        env=hermetic_env(XDG_CACHE_HOME=str(cache), GOH_MODEL_CHOICE=str(rules)), timeout=30,
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"].get("permissionDecision") != "deny", out
    return out


def _last_spawn(cache: Path) -> dict[str, Any]:
    lines = (cache / "goh" / "agent-spawns.jsonl").read_text().splitlines()
    return json.loads(lines[-1])


def _prompt(decl: str, want: int, mod: int = 2, shift: int = 0) -> str:
    """A prompt carrying `decl` whose sha256, shifted right by `shift` bits, is `want` mod `mod`."""
    for i in range(100_000):
        p = f"Fix the leak in foo.rs (variant {i}).\nRed test: t_leak.\nModel-choice: {decl}\n{POOL}"
        if (int(hashlib.sha256(p.encode()).hexdigest(), 16) >> shift) % mod == want:
            return p
    raise AssertionError("no prompt found")


def _spawn(model: str, effort: str, prompt: str) -> dict[str, Any]:
    return {"description": "fix it", "prompt": prompt, "subagent_type": "general-purpose",
            "model": model, "effort": effort}  # fmt: skip


def test_a_cheap_spawn_runs_on_the_tier_its_sha_parity_picks(tmp_path: Path) -> None:
    decl = "sonnet@high because checkable, known-fix"
    seen = {}
    for parity in (0, 1):
        cache = tmp_path / str(parity)
        out = _run(_spawn("sonnet", "high", _prompt(decl, parity)), cache)
        new = out["hookSpecificOutput"]["updatedInput"]
        seen[parity] = (new["model"], new["effort"])
        rec = _last_spawn(cache)
        assert rec["explored"] is True and rec["declared"] == "sonnet@high"
        assert rec["route"] == "cheap-alternation"
        assert (rec["model"], rec["effort"]) == seen[parity]
        assert f"Model-choice: {new['model']}@{new['effort']} because" in new["prompt"]
    assert set(seen.values()) == {("sonnet", "high"), ("opus", "low")}


def test_the_route_is_deterministic(tmp_path: Path) -> None:
    prompt = _prompt("opus@low because checkable, known-fix", 0)
    routes = {
        _run(_spawn("opus", "low", prompt), tmp_path / str(i))["hookSpecificOutput"]["updatedInput"][
            "model"]
        for i in range(8)
    }  # fmt: skip
    assert routes == {"sonnet"}  # parity 0 -> the first of `between`, every time


def test_opus_to_cheap_is_off_by_default(tmp_path: Path) -> None:
    decl = "opus@medium because cross-module, checkable"
    for i in range(12):  # every residue of the 1-in-10 selector at least once
        prompt = _prompt(decl, i % 10, mod=10_000, shift=8) if i < 10 else _prompt(decl, i % 2)
        out = _run(_spawn("opus", "medium", prompt), tmp_path / str(i))
        new = out["hookSpecificOutput"]["updatedInput"]
        assert (new["model"], new["effort"]) == ("opus", "medium")
        assert _last_spawn(tmp_path / str(i))["explored"] is False


def _rules_with_opus_to_cheap(tmp_path: Path, fraction: float) -> Path:
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    data["exploration"]["opus_to_cheap"].update(enabled=True, fraction=fraction)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(data))
    return path


def test_opus_to_cheap_when_on_routes_its_fraction_of_checkable_spawns(tmp_path: Path) -> None:
    rules = _rules_with_opus_to_cheap(tmp_path, 0.1)
    decl = "opus@medium because cross-module, checkable"
    picked = _prompt(decl, 0, mod=10_000, shift=8)  # selector 0 < 1000: inside the fraction
    out = _run(_spawn("opus", "medium", picked), tmp_path / "in", rules)
    new = out["hookSpecificOutput"]["updatedInput"]
    assert f"{new['model']}@{new['effort']}" in {"sonnet@high", "opus@low"}
    rec = _last_spawn(tmp_path / "in")
    assert rec["explored"] is True and rec["route"] == "opus-to-cheap"
    left = _prompt(decl, 5_000, mod=10_000, shift=8)  # outside the fraction
    out = _run(_spawn("opus", "medium", left), tmp_path / "out", rules)
    assert out["hookSpecificOutput"]["updatedInput"]["model"] == "opus"
    assert out["hookSpecificOutput"]["updatedInput"]["effort"] == "medium"


def test_nothing_without_the_mechanical_check_is_ever_routed_down(tmp_path: Path) -> None:
    rules = _rules_with_opus_to_cheap(tmp_path, 1.0)  # route everything it may
    for i, decl in enumerate(("opus@medium because cross-module",
                              "opus@medium because cross-module, checkable, unchecked")):  # fmt: skip
        out = _run(_spawn("opus", "medium", _prompt(decl, 0)), tmp_path / str(i), rules)
        new = out["hookSpecificOutput"]["updatedInput"]
        assert (new["model"], new["effort"]) == ("opus", "medium"), decl
        assert _last_spawn(tmp_path / str(i))["explored"] is False


def test_the_mechanical_check_bound_holds_even_if_the_rules_file_drops_it(tmp_path: Path) -> None:
    data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    data["exploration"]["opus_to_cheap"].update(enabled=True, fraction=1.0, requires_tags=[],
                                                refuses_tags=[])  # fmt: skip
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps(data))
    out = _run(_spawn("opus", "medium", _prompt("opus@medium because cross-module", 0)),
               tmp_path / "c", rules)  # fmt: skip
    new = out["hookSpecificOutput"]["updatedInput"]
    assert (new["model"], new["effort"]) == ("opus", "medium")
