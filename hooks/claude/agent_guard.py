#!/usr/bin/env python3
"""A Claude Code hook on subagent spawns: name the model, carry the report contract, keep a ledger.

Subagents spend most of the tokens, so economy is set where a session delegates (owner ruling
2026-10-18, `~/.claude/CLAUDE.md` "Delegation is where the tokens go"). Its rules 1, 3 and 4 were
kept by hand; this hook keeps them structurally (tests/test_claude_agent_guard.py):

* PreToolUse, matcher `Agent|Task`: a spawn must name its `model` AND `effort` (an unnamed one
  inherits the session's model at Claude Code's `medium`, Sonnet's measured weak spot), and its
  prompt must declare the choice -- `Model-choice: <model>@<effort> because <tags>` -- in terms
  the rules file's `tiers` accept (`model_choice.json`, read through `model_choice.py`; the
  researched rule is `~/.claude/skills/agent-fanout/references/model-choice-decision.md`).
  Anything else is refused with the table in the reason and the header that would have passed
  (`goh brief`, brief.py, GOH.BRIEFHEADER), so the caller fixes it in one try. A spawn it
  allows gets the report contract appended to its prompt (once: a respawn carrying it is left
  alone). Agent types where the choice does not matter (`exempt_agent_types`) are exempt.
* Profiles (the rules file's `profiles`, GOH.ULTRARULES): the tables in force are the owner's
  declared profile (pro, max, ultra), dropped one step by the budget guard when token-pool's Claude
  7-day meter is low, stale or unreadable (`model_choice.active_profile`). Every refusal goes
  through `_deny`, which names the active profile and why it is active; the spawn line keeps
  `profile`, `profile_why` and `meter`, the outcome line `load` and `cores` (the weekly usage
  review, scripts/usage_review.py, reads them).
* Free pools first (the active profile's `pool_first`, CLAUDE.md delegation rule 4): a spawn on
  any tier it names (max and pro: every tier but `never`; ultra: read-only lookups only) must say
  in its prompt that token-pool's `ask` was tried (`Pool: tried <...>`) or why it cannot do the
  work (`Pool: unfit because <...>`); the ledger keeps the line as `pool`.
* Lean agent types (the rules file's `lean_types`, GOH.LEANAGENT): a write spawn of a heavy type
  (`general-purpose`, whose first turn is ~70k tokens of MCP tool names and lists it never uses)
  is refused with the lean type goh installs (`repo-worker`, or `repo-worker-ui` for on-screen
  QA); `Agent-type: <type> because <reason>` in the prompt is the stated escape. A read-only
  lookup is not steered.
* Bounded exploration (the rules file's `exploration`): a spawn declared on a cheap tier runs on
  the cheap tier its prompt sha's parity picks, and -- only when the owner turns it on -- a fixed
  fraction of checkable spawns in the classes `opus_to_cheap.from` names runs on the cheap tier.
  Either is an `updatedInput` with the new model and effort, and `explored: true` on the ledger
  line. A spawn without the `checkable` tag is never routed down
  (tests/test_agent_guard_exploration.py).
* A brief that asks its agent to `goh land --enqueue` names `goh land --check` before it, or is
  refused (ci_judge.py `judge_land`, GOH.RULESLEFT): lanes skipped the check no brief asked for.
* Adaptive lanes (GOH.ADAPTLANES, `lane_room.py`): a write spawn asks lib/capacity.py's reading
  (`goh capacity`) whether the machine has room for another lane; GOH_LANE_ADMIT `refuse`
  (default) defers it naming what the reading lacked, `warn` says so and admits, an `Owner-asked:`
  line is deferred too (admitted in warn mode) and refused above the hard ceiling (GOH.OWNERASKCAP),
  a failed reading admits; goh.db's `lane_starts`.
* Every allowed spawn is one row of goh.db's `agent_spawns` (lib/hook_ledgers.py); SubagentStop
  adds the outcome row -- tokens summed from the agent's transcript, the report's `Status:` and the
  report itself (the audit checks its shas) -- so a wrong choice either way is visible as data.
* A fixed-name file the agent wrote straight into the shared scratchpad root is named at
  SubagentStop (GOH.AGENTSCRATCH, `scratch_guard.py`): on the outcome line (`scratch_loose`) and
  in a message to the session, with the per-item path to claim instead.
* A direct `gh run watch/view/list` or `gh api .../actions` call the agent ran is named at
  SubagentStop (GOH.GHDOOR, `ci_door.py`): on the outcome line (`ci_direct`) and to the session,
  with loopctl's one CI door to use instead.
* The owner's apps are watched (GOH.APPSTATE, `appstate.py`): the spawn line records which
  login-item apps were running; at SubagentStop any of them no longer running is named on the
  outcome line (`apps`) and in a message to the session, never relaunched (GOH.APPRESTOREOWNER).

Output shape, proven live on the Claude Code version the rules file's `proven_on` names: a refusal
is `hookSpecificOutput`
`permissionDecision: "deny"` + `permissionDecisionReason` (the reason reaches the model as the
tool's error); `updatedInput` ALONE replaces the tool input, so no `permissionDecision: "allow"`
is sent and the permission system decides exactly what it would have without this hook.

It fails OPEN: input it cannot read, a rules file it cannot read, or a ledger it cannot write,
never blocks a spawn.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))  # hook_ledgers (STATEDBS4)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # its siblings, whoever imports it
import appstate  # the owner's login-item apps, put back after an agent (GOH.APPSTATE)
import brief  # the corrected header a refusal carries (GOH.BRIEFHEADER)
import ci_door  # a direct gh CI read past loopctl's door (GOH.GHDOOR)
import hook_ledgers  # lib/: goh.db's hook ledgers, the spawn ledger's one door
import lane_room  # a write spawn starts only while the machine has room (GOH.ADAPTLANES)
import model_choice  # the rules file's one reader, beside this script
import scratch_guard  # a fixed-name file in the shared scratchpad root (GOH.AGENTSCRATCH)
from ci_judge import (
    judge_ci as _judge_ci,  # the CI-watching judge, beside this script
    judge_land as _judge_land,  # a land enqueued with no check (GOH.RULESLEFT)
)
from report_faults import read_faults  # the report's `Faults seen:` (LOOP.FAULTBULLETS)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tui.lib import noun  # the count meets its noun (GOH.HEDGEOWN)

SPAWN_TOOLS = {"Agent", "Task"}  # `Task` is the tool's legacy name

CONTRACT = (
    "Report contract: your final report is at most 15 lines. Its first line is "
    "`Status: landed|blocked|failed|answered`; then commits (sha + one line each) and how they "
    "left you: `goh ship` started with its result path (private repos), or pushed and tagged "
    "(public repos), red->green proof in one sentence, what is left open in one line, then "
    "`Faults seen: <each fault you noticed but did not fix (pre-existing WARN, error, red "
    "check, stale state), ';'-separated, each with where it is; or none>`. No narrative."
)
STATUSES = ("landed", "blocked", "failed", "answered")
STATUS_LINE = re.compile(r"\s*status:\s*(\w+)\s*$", re.IGNORECASE)
REPORT_CAP = 4000  # characters of the report kept on the outcome line (the audit reads its shas)

DECLARED = re.compile(r"^([ \t>*-]*)Model-choice:[ \t]*(\S+?)@(\S+)[ \t]+because[ \t]+(.*?)[ \t]*$",
                      re.MULTILINE)  # fmt: skip
FORMAT = "`Model-choice: <model>@<effort> because <tags>`"
POOL_LINE = re.compile(r"^[ \t>*-]*Pool:[ \t]*(tried|unfit[ \t]+because)\b[ \t]*(.*?)[ \t]*$",
                       re.MULTILINE)  # fmt: skip


def judge_pool(declared: str, prompt: str, rules: model_choice.Rules) -> tuple[str | None, Any]:
    """(refusal reason or None, what the `Pool:` line said) for a spawn the table accepted.

    Work on a tier `pool_first` names (every tier a spawn may take) goes to the free pools first:
    the prompt says it tried them, or why they cannot do this work."""
    task_class = rules.pool_class(*declared.split("@"))
    found = [(said.split()[0], why) for said, why in POOL_LINE.findall(prompt) if why]
    if task_class is None:
        return None, None
    tool = rules.pool_first["tool"]
    if found and found[0][1].startswith("<fill in"):  # goh brief's placeholder (GOH.BRIEFPOOLFILL)
        return (
            f"Refused: the `Pool:` line still carries goh brief's `<fill in ...>` placeholder, "
            f'which is no pool try. Replace it with what {tool}(task_class="{task_class}") '
            f"returned (`Pool: tried <its answer, or why it was not accepted>`), or say "
            f"`Pool: unfit because <reason>` when the work needs this repo's tools."
        ), None
    if found:
        said, why = found[0]
        return None, {"said": said, "why": why}
    return (
        f"Refused: {declared} work goes to the free pools first ({tool}, owner ruling in "
        f'{rules.path.name} `pool_first`). Call {tool}(task_class="{task_class}", prompt=..., '
        f"files=[...], check=... or schema=...) and spawn only if it does not return an accepted "
        f"answer; then add `Pool: tried <what it returned>` to this prompt. When the work needs "
        f"this repo's tools (it edits, builds or runs tests; pool's backends get the prompt and "
        f"the inlined files alone), add `Pool: unfit because <reason>` instead."
    ), None


TYPE_ESCAPE = re.compile(r"^[ \t>*-]*Agent-type:[ \t]*(\S+)[ \t]+because[ \t]+(\S.*?)[ \t]*$",
                         re.MULTILINE)  # fmt: skip


def judge_type(agent_type: str, declared: str, prompt: str,
               rules: model_choice.Rules) -> tuple[str | None, str | None]:  # fmt: skip
    """(refusal reason or None, the stated escape's reason) for a spawn the table accepted."""
    lean = rules.lean
    if agent_type not in (lean.get("from") or []):
        return None, None
    rule = rules.rule(*declared.split("@"))
    if rule is not None and rule["tier"] in (lean.get("read_only_tiers") or []):
        return None, None
    why = next((w for t, w in TYPE_ESCAPE.findall(prompt) if t == agent_type), None)
    if why:
        return None, why
    return (
        f"Refused: a write spawn of {agent_type} carries ~70.6k tokens of fixed preamble every "
        f"turn (every MCP server's tools and lists it never uses; measured 2026-11-08, "
        f'{rules.path.name} `lean_types`). Spawn subagent_type "{lean["to"]}" instead, or '
        f'"{lean["ui"]}" when the work needs on-screen QA (computer-use, browser, simulator). '
        f"If this session cannot see that type (it started before `python3 "
        f"scripts/apply_claude_hooks.py` installed it), add `Agent-type: {agent_type} because "
        f"<reason>` to the prompt."
    ), None


def family(model: str, rules: model_choice.Rules) -> str | None:
    """The alias a model names -- `sonnet` for `sonnet` or `claude-sonnet-5-5[1m]` -- else None."""
    return model_choice.family(model, rules.families)


def judge_choice(
    model: str, effort: str, prompt: str, rules: model_choice.Rules
) -> tuple[str | None, dict[str, Any]]:
    """(refusal reason or None, what the spawn declared) for a non-exempt spawn."""
    info: dict[str, Any] = {"model": family(model, rules), "effort": effort or None, "tags": []}
    if not model:
        return "it names no model, so it would inherit the session's", info
    if not effort:
        return "it names no effort, so it would run at Claude Code's medium", info
    if info["model"] is None:
        return f"unknown model {model!r} (aliases: {', '.join(rules.families)})", info
    if effort not in rules.efforts:
        return f"unknown effort {effort!r} (one of: {', '.join(rules.efforts)})", info
    decls = {(m.lower(), e.lower(), t) for _, m, e, t in DECLARED.findall(prompt)}
    if not decls:
        return "the prompt has no `Model-choice:` line", info
    if len(decls) > 1:
        return "its `Model-choice:` lines disagree; keep one", info
    ((d_model, d_effort, because),) = decls
    tags = [t for t in re.split(r"[\s,+]+", because) if t]
    info["tags"] = tags
    if (family(d_model, rules), d_effort) != (info["model"], effort):
        return (
            f"the declared {d_model}@{d_effort} disagrees with the spawn's {model}@{effort}",
            info,
        )
    unknown = sorted(set(tags) - rules.tags)
    if unknown:
        return f"unknown {noun(len(unknown), 'tag')} {', '.join(unknown)}", info
    rule = rules.rule(info["model"], effort)
    cls = f"{info['model']}@{effort}"
    if rule is None:
        return f"{cls} is not in the table", info
    if rule["tier"] == "never":
        return f"{cls} is never chosen: {rule['why']}", info
    if not rules.accepts(rule, set(tags)):
        need = "all of" if rule["need"] == "all" else "one of"
        also = f", or all of: {', '.join(rule['also_all'])}" if rule.get("also_all") else ""
        return f"{cls} needs {need}: {', '.join(sorted(rule['tags']))}{also}", info
    return None, info


def _append(record: dict[str, Any]) -> str | None:
    """One spawn ledger row in goh.db (lib/hook_ledgers.py: a 5 s wait, then spilled and counted);
    the warning to show when goh.db did not take it, else None. Never blocks the spawn."""
    return hook_ledgers.append(hook_ledgers.SPAWNS, record)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200]


def _routed(prompt: str, target: str, why: str, declared: str) -> str:
    """The prompt with its `Model-choice:` line naming the tier it now runs on."""
    model, effort = target.split("@")

    def swap(m: re.Match[str]) -> str:
        return (f"{m.group(1)}Model-choice: {model}@{effort} because {m.group(4)} "
                f"(routed by agent_guard {why}; declared {declared})")  # fmt: skip

    return DECLARED.sub(swap, prompt)


def _deny(reason: str, rules: model_choice.Rules) -> dict[str, Any]:
    """The refusal, naming the active profile and why it is active (GOH.ULTRARULES): the one way
    this hook refuses, so no refusal can leave the profile out."""
    line = rules.profile_line()
    reason = f"{reason}\nRules in force: {line}." if line else reason
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}  # fmt: skip


def _refuse(reason: str, rules: model_choice.Rules, prompt: str, agent_type: str) -> dict[str, Any]:
    """A header refusal, carrying the header that would have passed (goh brief, GOH.BRIEFHEADER)."""
    fix = brief.corrected(rules, prompt, agent_type, DECLARED, POOL_LINE, TYPE_ESCAPE,
                          lane_room.owner_words(prompt))  # fmt: skip
    return _deny(f"{reason}\n{fix}", rules)


def _meter(reading: Any) -> dict[str, Any] | None:
    """The meter figures a ledger line keeps (the weekly review's minimum reads them)."""
    if not isinstance(reading, dict):
        return None
    return {k: v for k, v in reading.items() if k != "why"}


def _load() -> list[float] | None:
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def on_spawn(event: dict[str, Any]) -> dict[str, Any] | None:
    """The PreToolUse output for an Agent/Task call, or None to let it run untouched."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None  # not a shape this hook knows: unjudged, never blocked
    try:
        rules = model_choice.load()
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"systemMessage": f"agent_guard: the model-choice rules file could not be read "
                                 f"({e!r}); this spawn was not judged."}  # fmt: skip
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    model, effort = (
        v.strip() if isinstance(v, str) else ""
        for v in (tool_input.get("model"), tool_input.get("effort"))
    )
    exempt = agent_type in rules.exempt
    info: dict[str, Any] = {"model": family(model, rules), "effort": effort or None, "tags": []}
    if not exempt:
        why, info = judge_choice(model, effort, prompt, rules)
        if why:
            return _refuse(
                f"Refused: this {agent_type} spawn -- {why}. Name model AND effort, and declare "
                f"them in the prompt as {FORMAT}, tags from this table:\n{rules.table_text()}",
                rules, prompt, agent_type,
            )  # fmt: skip
    out: dict[str, Any] = {}
    final, new_input = prompt, dict(tool_input)
    declared = f"{info['model']}@{info['effort']}"
    pool = None
    if not exempt:
        why, pool = judge_pool(declared, prompt, rules)
        if why:
            return _refuse(why, rules, prompt, agent_type)
    if not exempt and (why := _judge_ci(prompt, CONTRACT) or _judge_land(prompt, CONTRACT)):
        return _deny(why, rules)
    escape = None
    if not exempt:
        why, escape = judge_type(agent_type, declared, prompt, rules)
        if why:
            return _refuse(why, rules, prompt, agent_type)
    lane = lane_room.Verdict()
    tier = None if exempt else (rules.rule(*declared.split("@")) or {}).get("tier")
    if lane_room.is_write(tier, rules.lean.get("read_only_tiers") or []):
        lane = lane_room.judge(event, agent_type, declared)
        if lane.deny:
            return _deny(lane.deny, rules)
    route = None
    if not exempt:
        route = rules.route(declared, info["tags"], hashlib.sha256(prompt.encode()).hexdigest())
    if route is not None and route[0] != declared:  # the tier it picked may be the one declared
        target, why = route
        final = _routed(prompt, target, why, declared)
        info["model"], info["effort"] = target.split("@")
        new_input.update(model=info["model"], effort=info["effort"])
    if not exempt and CONTRACT not in final:
        final = final.rstrip() + "\n\n" + CONTRACT
    if final != prompt or new_input != tool_input:
        new_input["prompt"] = final
        out["hookSpecificOutput"] = {"hookEventName": "PreToolUse", "updatedInput": new_input}
    warning = _append({
        "event": "spawn", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": event.get("tool_use_id"), "model": info["model"],
        "model_id": new_input.get("model") or None, "effort": info["effort"], "tags": info["tags"],
        "subagent_type": agent_type, "exempt": exempt, "description": tool_input.get("description"),
        "declared": None if exempt else declared, "explored": route is not None,
        "route": route[1] if route else None, "pool": pool, "agent_type_escape": escape,
        "prompt_sha": hashlib.sha256(final.encode()).hexdigest(), "first_line": _first_line(prompt),
        "apps": appstate.snapshot(event.get("tool_use_id")),
        "profile": rules.profile or None, "profile_why": rules.profile_why,
        "meter": _meter(rules.meter), "lane": (lane.record or {}).get("verdict"),
    })  # fmt: skip
    if said := " ".join(m for m in (lane.message, warning) if m):
        out["systemMessage"] = said
    return out or None


# How long SubagentStop waits for the agent's closing entry to reach its transcript. The event
# fires BEFORE that entry is written (live, on `proven_on`'s version: the hook saw only the
# thinking entry; the text entry, with most of the output tokens, landed within 2 s), and the
# write runs beside the hook.
SETTLE_S = 5.0


def _entries(text: str) -> list[dict[str, Any]]:
    out = []
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("message"), dict):
            out.append(entry["message"])
    return out


def _has_report(text: str, report: str) -> bool:
    want = report.strip()
    return any(
        isinstance(c, dict) and c.get("type") == "text" and str(c.get("text", "")).strip() == want
        for msg in _entries(text)
        if isinstance(msg.get("content"), list)
        for c in msg["content"]
    )


def settled_transcript(
    path: Path, report: str, deadline_s: float = SETTLE_S
) -> tuple[str | None, bool]:
    """(the transcript's text, whether it holds the agent's closing report). Reads until the report
    is there or the deadline passes: the transcript lags the event (SETTLE_S)."""
    end = time.monotonic() + deadline_s
    while True:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None, False
        if not report.strip() or _has_report(text, report):
            return text, True
        if time.monotonic() >= end:
            return text, False
        time.sleep(0.05)  # the next pass re-reads the transcript: a poll, not a guess


def usage(text: str) -> dict[str, int]:
    """Tokens the agent spent: usage summed over its assistant messages, each message ONCE and at
    its largest (a message streamed as several entries carries its usage on each, growing)."""
    keys = {"input": "input_tokens", "output": "output_tokens",
            "cache_creation": "cache_creation_input_tokens",
            "cache_read": "cache_read_input_tokens"}  # fmt: skip
    per: dict[str, dict[str, int]] = {}
    for i, msg in enumerate(_entries(text)):
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        cur = per.setdefault(str(msg.get("id") or f"#{i}"), dict.fromkeys(keys, 0))
        for k, v in keys.items():
            cur[k] = max(cur[k], int(u.get(v) or 0))
    tokens = {k: sum(m[k] for m in per.values()) for k in keys}
    tokens["total"] = sum(tokens.values())
    return tokens


def _status(report: str) -> str:
    m = STATUS_LINE.match(report.splitlines()[0]) if report.strip() else None
    word = m.group(1).lower() if m else ""
    return word if word in STATUSES else "unreported"


def on_stop(event: dict[str, Any]) -> list[str]:
    """Record a finished subagent's outcome, naming the login-item apps not running at its stop that
    ran at its spawn; the messages for the session (apps gone, a ledger not written). Never blocks
    the stop."""
    raw_path = event.get("agent_transcript_path")
    transcript = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
    tool_use_id = None
    if transcript is not None:
        meta = transcript.with_name(transcript.name.removesuffix(".jsonl") + ".meta.json")
        try:
            tool_use_id = json.loads(meta.read_text(encoding="utf-8")).get("toolUseId")
        except (OSError, ValueError, AttributeError):
            tool_use_id = None
    apps = appstate.gone(tool_use_id)  # read first: the stop's state, before the transcript wait
    report = event.get("last_assistant_message")
    report = report if isinstance(report, str) else ""
    text, settled = settled_transcript(transcript, report) if transcript else (None, False)
    found_faults, unread = read_faults(report)
    loose = scratch_guard.loose_writes(text) if text is not None else []
    ci = ci_door.direct_calls(text) if text is not None else []
    warning = _append({
        "event": "outcome", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": tool_use_id, "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "status": _status(report), "report": report[:REPORT_CAP] or None,
        "faults": found_faults,
        "tokens": usage(text) if text is not None else None,
        "tokens_settled": settled, "apps": apps, "scratch_loose": loose,
        "ci_direct": ci, "load": _load(), "cores": os.cpu_count(),
    })  # fmt: skip
    scratch = scratch_guard.message(loose) if loose else None
    door = ci_door.message(ci) if ci else None
    gone = appstate.describe(apps, event.get("agent_id"))
    return [m for m in (gone, scratch, door, unread, warning) if m]


def main() -> int:
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            return 0
        name = event.get("hook_event_name")
        if name == "PreToolUse" and event.get("tool_name") in SPAWN_TOOLS:
            out = on_spawn(event)
            if out:
                json.dump(out, sys.stdout)
        elif name == "SubagentStop":
            messages = on_stop(event)
            if messages:
                json.dump({"systemMessage": " ".join(messages)}, sys.stdout)
    except Exception as e:  # noqa: BLE001 -- fail OPEN: a guard bug must never block every spawn
        print(f"agent_guard: input not judged ({e!r})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
