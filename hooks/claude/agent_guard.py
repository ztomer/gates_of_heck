#!/usr/bin/env python3
"""A Claude Code hook on subagent spawns: name the model, carry the report contract, keep a ledger.

Subagents spend most of the tokens, so economy is set where a session delegates (owner ruling
2026-10-18, `~/.claude/CLAUDE.md` "Delegation is where the tokens go"). Its rules 1, 3 and 4 were kept
by hand; this hook keeps them structurally (tests/test_claude_agent_guard.py):

* PreToolUse, matcher `Agent|Task`: a spawn must name its `model` AND `effort` (an unnamed one
  inherits the session's model at Claude Code's `medium`, Sonnet's measured weak spot), and its
  prompt must declare the choice -- `Model-choice: <model>@<effort> because <tags>` -- in terms
  the rules file's `tiers` accept (`model_choice.json`, read through `model_choice.py`; the
  researched rule is `~/.claude/skills/agent-fanout/references/model-choice-decision.md`).
  Anything else is refused with the table in the reason, so the caller fixes it in one try. A
  spawn it allows gets the report contract appended to its prompt (once: a respawn carrying it is
  left alone). Agent types where the choice does not matter (`exempt_agent_types`) are exempt.
* Free pools first (the rules file's `pool_first`, CLAUDE.md delegation rule 4): a spawn on a cheap
  or lookup tier must say in its prompt that token-pool's `ask` was tried (`Pool: tried <...>`) or
  why it cannot do the work (`Pool: unfit because <...>`); the ledger keeps the line as `pool`.
* Bounded exploration (the rules file's `exploration`): a spawn declared on a cheap tier runs on
  the cheap tier its prompt sha's parity picks, and -- only when the owner turns it on -- a fixed
  fraction of checkable spawns in the classes `opus_to_cheap.from` names runs on the cheap tier. Either is an
  `updatedInput` with the new model and effort, and `explored: true` on the ledger line. A spawn
  without the `checkable` tag is never routed down (tests/test_agent_guard_exploration.py).
* Every allowed spawn is one line in `$XDG_CACHE_HOME/goh/agent-spawns.jsonl`; SubagentStop adds
  the outcome line -- tokens summed from the agent's transcript, the report's `Status:` and the
  report itself (the audit checks its shas) -- so a wrong choice either way is visible as data.

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

import model_choice  # the rules file's one reader, beside this script

SPAWN_TOOLS = {"Agent", "Task"}  # `Task` is the tool's legacy name

CONTRACT = (
    "Report contract: your final report is at most 15 lines. Its first line is "
    "`Status: landed|blocked|failed|answered`; then commits (sha + one line each), CI run id + "
    "conclusion, red->green proof in one sentence, what is left open in one line, then `Faults seen: <each fault you noticed but did not fix "
    "(pre-existing WARN, error, red check, stale state), ';'-separated, each with where it is; "
    "or none>`. No narrative."
)
FAULTS_LINE = re.compile(
    r"^[ \t>*-]*Faults[ \t]+seen:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE
)
NO_FAULTS = {"none", "none.", "-", "n/a"}
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

    Cheap and lookup work goes to the free pools first (the rules file's `pool_first`): the
    prompt says it tried them, or why they cannot do this work. Other tiers need no line."""
    task_class = rules.pool_class(*declared.split("@"))
    found = [(said.split()[0], why) for said, why in POOL_LINE.findall(prompt) if why]
    if task_class is None:
        return None, None
    if found:
        said, why = found[0]
        return None, {"said": said, "why": why}
    tool = rules.pool_first["tool"]
    return (
        f"Refused: {declared} work goes to the free pools first ({tool}, owner ruling in "
        f'{rules.path.name} `pool_first`). Call {tool}(task_class="{task_class}", prompt=..., '
        f"check=... or schema=...) and spawn only if it does not return an accepted answer; then "
        f"add `Pool: tried <what it returned>` to this prompt. When the work needs this repo's "
        f"files or tools (pool's backends get the prompt alone), add `Pool: unfit because "
        f"<reason>` instead."
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
        return f"unknown tag(s) {', '.join(unknown)}", info
    rule = rules.rule(info["model"], effort)
    cls = f"{info['model']}@{effort}"
    if rule is None:
        return f"{cls} is not in the table", info
    if rule["tier"] == "never":
        return f"{cls} is never chosen: {rule['why']}", info
    need = set(rule["tags"])
    if rule["need"] == "all" and not need <= set(tags):
        return f"{cls} needs all of: {', '.join(sorted(need))}", info
    if rule["need"] == "any" and not need & set(tags):
        return f"{cls} needs one of: {', '.join(sorted(need))}", info
    return None, info


def ledger_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    path = Path(base) / "goh" / "agent-spawns.jsonl"
    # Under goh's own suite a ledger row in the USER's cache corrupts the model-choice audit (G12):
    # refused loudly (not an OSError, so _append does not swallow it). lib/suite_cache_guard.sh.
    real = os.environ.get("GATES_OF_HECK_SUITE_REAL_HOME")
    if real:  # set by goh's tests/suite_home.py alone
        user = f"{real}/.cache/goh/"
        if f"{path}/".startswith(user) and not str(path).startswith(f"{user}suite/"):
            raise RuntimeError(
                f"agent_guard under goh's suite would write the user's ledger: {path}"
            )
    return path


def _append(record: dict[str, Any]) -> str | None:
    """Append one ledger line; the warning to show when it could not be written, else None."""
    try:
        path = ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True) + "\n"
        with path.open("a", encoding="utf-8") as f:  # one write: O_APPEND keeps lines whole
            f.write(line)
    except OSError as e:
        return f"agent_guard: the spawn ledger was not written ({e}); the spawn is not blocked."
    return None


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
            reason = (
                f"Refused: this {agent_type} spawn -- {why}. Name model AND effort, and declare "
                f"them in the prompt as {FORMAT}, tags from this table:\n{rules.table_text()}"
            )
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}  # fmt: skip
    out: dict[str, Any] = {}
    final, new_input = prompt, dict(tool_input)
    declared = f"{info['model']}@{info['effort']}"
    pool = None
    if not exempt:
        why, pool = judge_pool(declared, prompt, rules)
        if why:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": why}}  # fmt: skip
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
        "route": route[1] if route else None, "pool": pool,
        "prompt_sha": hashlib.sha256(final.encode()).hexdigest(), "first_line": _first_line(prompt),
    })  # fmt: skip
    if warning:
        out["systemMessage"] = warning
    return out or None


# How long SubagentStop waits for the agent's closing entry to reach its transcript. The event
# fires BEFORE that entry is written (live, on `proven_on`'s version: the hook saw only the thinking entry; the text
# entry, with most of the output tokens, landed within 2 s), and the write runs beside the hook.
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
            "cache_creation": "cache_creation_input_tokens", "cache_read": "cache_read_input_tokens"}  # fmt: skip
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


def faults(report: str) -> list[str] | None:
    """The report's `Faults seen:` line as a list: [] for none, None when the line is absent
    (not stated, which is not the same claim as none)."""
    m = FAULTS_LINE.search(report)
    if m is None:
        return None
    body = m.group(1).strip().strip("`").strip()
    if body.lower() in NO_FAULTS or not body:
        return []
    return [f.strip() for f in body.split(";") if f.strip()]


def on_stop(event: dict[str, Any]) -> str | None:
    """Record a finished subagent's outcome; the ledger warning, if any. Never blocks the stop."""
    raw_path = event.get("agent_transcript_path")
    transcript = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
    tool_use_id = None
    if transcript is not None:
        meta = transcript.with_name(transcript.name.removesuffix(".jsonl") + ".meta.json")
        try:
            tool_use_id = json.loads(meta.read_text(encoding="utf-8")).get("toolUseId")
        except (OSError, ValueError, AttributeError):
            tool_use_id = None
    report = event.get("last_assistant_message")
    report = report if isinstance(report, str) else ""
    text, settled = settled_transcript(transcript, report) if transcript else (None, False)
    return _append({
        "event": "outcome", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": tool_use_id, "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "status": _status(report), "report": report[:REPORT_CAP] or None,
        "faults": faults(report),
        "tokens": usage(text) if text is not None else None,
        "tokens_settled": settled,
    })  # fmt: skip


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
            warning = on_stop(event)
            if warning:
                print(warning, file=sys.stderr)
    except Exception as e:  # noqa: BLE001 -- fail OPEN: a guard bug must never block every spawn
        print(f"agent_guard: input not judged ({e!r})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
