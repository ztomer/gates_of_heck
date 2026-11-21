"""`goh brief` generates a spawn's header; the spawn guard's refusal carries the corrected one.

55 spawn refusals in 7 days (to 2026-11-21) were brief headers written by trial (GOH.BRIEFHEADER).
The header is generated from `hooks/claude/model_choice.json`'s active profile, read through
`model_choice.load()` as the guard reads it, so (1) `goh brief` output passes the spawn guard for
every tier of every profile, and (2) every refusal shape seen that week gets a corrected header in
its refusal that, applied to the refused prompt, passes the guard.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import brief
import model_choice

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
BRIEF = GUARD.with_name("brief.py")
RULES = json.loads(GUARD.with_name("model_choice.json").read_text(encoding="utf-8"))
GUARD_CFG = RULES["profiles"]["budget_guard"]
TASK = "Fix the leak in foo.rs.\nRed test: t_leak."


def _env(tmp: Path, declared: str) -> dict[str, str]:
    """A hermetic env whose rules file declares `declared` and whose meter keeps it active."""
    pool = tmp / "pool"
    pool.mkdir(parents=True, exist_ok=True)
    when = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    doc = {
        "schema_version": 1,
        "read_at": when,
        "sources": [
            {
                "id": GUARD_CFG["source"],
                "name": "Claude",
                "state": "value",
                "short": "",
                "why": "5h: 90% left; 7d: 95% left",
                "read_at": when,
            }
        ],
    }
    (pool / GUARD_CFG["meter_file"]).write_text(json.dumps(doc))
    data = json.loads(json.dumps(RULES))
    data["profiles"]["declared"] = declared
    rules = tmp / f"rules-{declared}.json"
    rules.write_text(json.dumps(data))
    return hermetic_env(
        XDG_CACHE_HOME=str(tmp / "cache"), POOL_STATE_DIR=str(pool), GOH_MODEL_CHOICE=str(rules)
    )


def _guard(env: dict[str, str], prompt: str, model: str, effort: str, sub: str) -> str | None:
    """The guard's refusal reason for this spawn, or None when it is allowed."""
    tool_input = {"description": "d", "prompt": prompt, "subagent_type": sub}
    tool_input |= {k: v for k, v in (("model", model), ("effort", effort)) if v}
    event = {
        "session_id": "s",
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_use_id": "t",
        "tool_input": tool_input,
    }
    r = run_hook(GUARD, json.dumps(event), env)
    assert r.returncode == 0, r.stderr
    hso = (json.loads(r.stdout) if r.stdout.strip() else {}).get("hookSpecificOutput") or {}
    return hso.get("permissionDecisionReason") if hso.get("permissionDecision") == "deny" else None


def _brief(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(BRIEF), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _tiers(declared: str) -> list[tuple[str, str, str, list[str]]]:
    """(work, family, effort, tags) for every class a spawn may take under `declared`."""
    rules = model_choice.Rules(RULES, Path("x"), profile=declared)
    read_only = set(rules.lean["read_only_tiers"])
    out = []
    for fam in rules.families:
        for eff in rules.efforts:
            rule = rules.rule(fam, eff)
            if rule is None or rule["tier"] == "never":
                continue
            tags = rule["tags"] if rule["need"] == "all" else rule["tags"][:1]
            out.append(("read" if rule["tier"] in read_only else "write", fam, eff, list(tags)))
    return out


@pytest.mark.parametrize("declared", RULES["profiles"]["order"])
def test_goh_brief_output_passes_the_spawn_guard_for_every_tier(
    tmp_path: Path, declared: str
) -> None:
    env = _env(tmp_path, declared)
    tiers = _tiers(declared)
    assert len(tiers) > 5, tiers
    for work, fam, eff, tags in tiers:
        r = _brief(
            env,
            "--work",
            work,
            "--tags",
            ",".join(tags),
            "--model",
            fam,
            "--effort",
            eff,
            "--owner-asked",
            "Yes, dispatch them",
            "--json",
        )
        assert r.returncode == 0, (fam, eff, r.stderr)
        got = json.loads(r.stdout)
        assert (got["model"], got["effort"]) == (fam, eff), got
        assert declared in got["profile"], got
        prompt = "\n".join(got["header"]) + "\n" + TASK
        why = _guard(env, prompt, got["model"], got["effort"], got["subagent_type"])
        assert why is None, (declared, fam, eff, why)


def test_unpinned_brief_takes_the_cheapest_tier_the_profile_accepts(tmp_path: Path) -> None:
    env = _env(tmp_path, "ultra")
    r = _brief(env, "--work", "write", "--tags", "checkable,known-fix", "--json")
    got = json.loads(r.stdout)
    rules = model_choice.Rules(RULES, Path("x"), profile="ultra")
    assert rules.rule(got["model"], got["effort"])["tier"] == "cheap", got
    assert got["subagent_type"] == RULES["lean_types"]["to"], got
    r = _brief(env, "--work", "read", "--tags", "read-only", "--json")
    got = json.loads(r.stdout)
    assert rules.rule(got["model"], got["effort"])["tier"] == "lookup", got
    assert any(ln.startswith("Pool: tried") for ln in got["header"]), got


def test_brief_text_names_the_agent_call_and_refuses_bad_work(tmp_path: Path) -> None:
    env = _env(tmp_path, "ultra")
    r = _brief(env, "--work", "write", "--tags", "cross-module", "--repo", "goh")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("Model-choice: ") and "Agent call: model=" in r.stdout, r.stdout
    assert "Rules in force: profile ultra" in r.stderr, r.stderr
    assert _brief(env, "--work", "both", "--tags", "x").returncode == 2
    r = _brief(env, "--work", "write", "--tags", "cross-module", "--agent-type", "general-purpose")
    assert r.returncode == 2 and "--type-why" in r.stderr, r.stderr
    r = _brief(
        env,
        "--work",
        "write",
        "--tags",
        "cross-module",
        "--agent-type",
        "general-purpose",
        "--type-why",
        "the session predates repo-worker",
    )
    assert "Agent-type: general-purpose because the session predates" in r.stdout, r.stdout


# The refusal shapes of the week to 2026-11-21 (GOH.BRIEFHEADER): (declared profile, header lines
# the prompt carried, model, effort, subagent_type).
DECL = "Model-choice: opus@medium because cross-module"
SHAPES = {
    "no effort": ("ultra", [DECL], "opus", "", "repo-worker"),
    "no model": ("ultra", [DECL], "", "medium", "repo-worker"),
    "general-purpose for write work": ("ultra", [DECL], "opus", "medium", "general-purpose"),
    "unknown tags": (
        "ultra",
        ["Model-choice: opus@medium because bugfix, cross-module"],
        "opus",
        "medium",
        "repo-worker",
    ),
    "only unknown tags": (
        "ultra",
        ["Model-choice: opus@medium because bugfix"],
        "opus",
        "medium",
        "repo-worker",
    ),
    "sonnet@high under the profile": (
        "ultra",
        ["Model-choice: sonnet@high because checkable, known-fix"],
        "sonnet",
        "high",
        "repo-worker",
    ),
    "opus@medium without its tag": (
        "ultra",
        ["Model-choice: opus@medium because checkable"],
        "opus",
        "medium",
        "repo-worker",
    ),
    "missing Pool line (lookup)": (
        "ultra",
        ["Model-choice: haiku@low because read-only"],
        "haiku",
        "low",
        "general-purpose",
    ),
    "missing Pool line (max write)": ("max", [DECL], "opus", "medium", "repo-worker"),
    "no Model-choice line": ("ultra", [], "opus", "medium", "repo-worker"),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_refusal_carries_a_corrected_header_that_passes(tmp_path: Path, shape: str) -> None:
    declared, lines, model, effort, sub = SHAPES[shape]
    env = _env(tmp_path, declared)
    prompt = "\n".join([*lines, "Owner-asked: Yes, dispatch them", TASK])
    why = _guard(env, prompt, model, effort, sub)
    assert why is not None, f"{shape}: the guard allowed it"
    assert "Corrected header" in why, why
    header = why.split("Corrected header", 1)[1].split("\n", 1)[1]
    fixed, call = brief.apply(prompt, header)
    assert "Owner-asked: Yes, dispatch them" in fixed and TASK in fixed, fixed
    again = _guard(env, fixed, call["model"], call["effort"], call["subagent_type"])
    assert again is None, f"{shape}: the corrected header was refused too:\n{fixed}\n{again}"
