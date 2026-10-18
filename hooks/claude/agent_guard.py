#!/usr/bin/env python3
"""A Claude Code hook on subagent spawns: name the model, carry the report contract, keep a ledger.

Subagents spend most of the tokens, so economy is set where a session delegates (owner ruling
2026-10-18, `~/.claude/CLAUDE.md` "Delegation is where the tokens go"). Its rules 1 and 3 were kept
by hand; this hook keeps them structurally (tests/test_claude_agent_guard.py):

* PreToolUse, matcher `Agent|Task`: a spawn must name its `model` AND `effort` (an unnamed one
  inherits the session's model at Claude Code's `medium`, Sonnet's measured weak spot), and its
  prompt must declare the choice -- `Model-choice: <model>@<effort> because <tags>` -- in terms
  CHOICE_RULES accepts. Anything else is refused with the table in the reason, so the caller fixes
  it in one try. A spawn it allows gets the report contract appended to its prompt (once: a
  respawn carrying it is left alone). Agent types where the choice does not matter are exempt
  from all of it (MODEL_EXEMPT). The table is the researched rule in
  `~/.claude/skills/agent-fanout/references/model-choice-decision.md`.
* Every allowed spawn is one line in `$XDG_CACHE_HOME/goh/agent-spawns.jsonl`; SubagentStop adds
  the outcome line -- tokens summed from the agent's transcript, the report's `Status:` -- so a
  wrong model choice in either direction is visible as data.

Output shape, proven live on Claude Code 2.1.292: a refusal is `hookSpecificOutput`
`permissionDecision: "deny"` + `permissionDecisionReason` (the reason reaches the model as the
tool's error); `updatedInput` ALONE replaces the tool input, so no `permissionDecision: "allow"`
is sent and the permission system decides exactly what it would have without this hook.

It fails OPEN: input it cannot read, or a ledger it cannot write, never blocks a spawn.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SPAWN_TOOLS = {"Agent", "Task"}  # `Task` is the tool's legacy name

# Agent types where the model choice does not matter: read-only lookups and meta agents, and
# `fork`, which ignores `model`. They need no model and get no contract -- none of them lands
# commits, so commits / CI / red->green do not apply -- but their spawns are still ledgered.
MODEL_EXEMPT = frozenset({"Explore", "claude-code-guide", "statusline-setup", "fork"})

CONTRACT = (
    "Report contract: your final report is at most 15 lines. Its first line is "
    "`Status: landed|blocked|failed|answered`; then commits (sha + one line each), CI run id + "
    "conclusion, red->green proof in one sentence, what is left open in one line. No narrative."
)
STATUSES = ("landed", "blocked", "failed", "answered")
STATUS_LINE = re.compile(r"\s*status:\s*(\w+)\s*$", re.IGNORECASE)

EFFORTS = ("low", "medium", "high", "xhigh", "max")
FAMILIES = ("sonnet", "opus", "haiku", "fable")  # the aliases the Agent tool takes

# Why a cheap-tier choice is wrong from the start: the outcome is not mechanically checkable, or
# the path is unknown.
_OPUS_BECAUSE = frozenset({"unknown-root-cause", "design-ui", "cross-module", "audit-review",
                           "research", "security", "multi-session", "unchecked"})  # fmt: skip
_MEDIUM = "Claude Code's medium is Sonnet's measured weak spot (62.7% vs 77.8% at high)"
# THE choice table: `<model>@<effort>` (a `*` matches any) -> what the declared tags must hold.
# ("all", tags): every one; ("any", tags): at least one; ("never", why): refused whatever the tags.
CHOICE_RULES: dict[str, tuple[str, frozenset[str] | str]] = {
    "sonnet@high": ("all", frozenset({"checkable", "known-fix"})),
    "opus@low": ("all", frozenset({"checkable", "known-fix"})),
    "opus@medium": ("any", _OPUS_BECAUSE),
    "opus@high": ("any", _OPUS_BECAUSE),
    "opus@xhigh": ("all", frozenset({"escalation"})),
    "haiku@low": ("all", frozenset({"read-only"})),
    "haiku@medium": ("all", frozenset({"read-only"})),
    "fable@*": ("all", frozenset({"owner-asked"})),
    "*@max": ("all", frozenset({"owner-asked"})),
    "sonnet@low": ("never", _MEDIUM),
    "sonnet@medium": ("never", _MEDIUM),
    "sonnet@xhigh": ("never", "Sonnet at xhigh/max costs more than Opus for less"),
}
TAGS = frozenset().union(*(v for kind, v in CHOICE_RULES.values() if kind != "never"))
DECLARED = re.compile(r"^[ \t>*-]*Model-choice:[ \t]*(\S+?)@(\S+)[ \t]+because[ \t]+(.*?)[ \t]*$",
                      re.MULTILINE)  # fmt: skip
FORMAT = "`Model-choice: <model>@<effort> because <tags>`"


def family(model: str) -> str | None:
    """The alias a model names -- `sonnet` for `sonnet` or `claude-sonnet-5-5[1m]` -- else None."""
    words = re.split(r"[^a-z0-9]+", model.lower())
    return next((w for w in words if w in FAMILIES), None)


def _rule(fam: str, effort: str) -> tuple[str, frozenset[str] | str] | None:
    for key in (f"{fam}@{effort}", f"*@{effort}", f"{fam}@*"):
        if key in CHOICE_RULES:
            return CHOICE_RULES[key]
    return None


def table_text() -> str:
    """CHOICE_RULES in a few lines: rows with the same rule share one line."""
    rows: dict[tuple[str, str], list[str]] = {}
    for key, (kind, need) in CHOICE_RULES.items():
        if kind == "never":
            rows.setdefault(("never", ""), []).append(key)
            continue
        sep = " + " if kind == "all" else ", "
        text = sep.join(sorted(need)) if isinstance(need, frozenset) else need
        rows.setdefault((kind, text), []).append(key)
    lines = []
    for (kind, text), keys in rows.items():
        if kind == "never":
            whys = dict.fromkeys(str(CHOICE_RULES[k][1]) for k in keys)  # unique, in order
            lines.append(f"  never: {', '.join(keys)} -- {'; '.join(whys)}")
        else:
            lines.append(f"  {', '.join(keys)}: {'one of ' if kind == 'any' else ''}{text}")
    return "\n".join(lines)


def judge_choice(model: str, effort: str, prompt: str) -> tuple[str | None, dict[str, Any]]:
    """(refusal reason or None, what the spawn declared) for a non-exempt spawn."""
    info: dict[str, Any] = {"model": family(model), "effort": effort or None, "tags": []}
    if not model:
        return "it names no model, so it would inherit the session's", info
    if not effort:
        return "it names no effort, so it would run at Claude Code's medium", info
    if info["model"] is None:
        return f"unknown model {model!r} (aliases: {', '.join(FAMILIES)})", info
    if effort not in EFFORTS:
        return f"unknown effort {effort!r} (one of: {', '.join(EFFORTS)})", info
    decls = {(m.lower(), e.lower(), t) for m, e, t in DECLARED.findall(prompt)}
    if not decls:
        return "the prompt has no `Model-choice:` line", info
    if len(decls) > 1:
        return "its `Model-choice:` lines disagree; keep one", info
    ((d_model, d_effort, because),) = decls
    tags = [t for t in re.split(r"[\s,+]+", because) if t]
    info["tags"] = tags
    if (family(d_model), d_effort) != (info["model"], effort):
        return (
            f"the declared {d_model}@{d_effort} disagrees with the spawn's {model}@{effort}",
            info,
        )
    unknown = sorted(set(tags) - TAGS)
    if unknown:
        return f"unknown tag(s) {', '.join(unknown)}", info
    rule = _rule(info["model"], effort)
    if rule is None:
        return f"{info['model']}@{effort} is not in the table", info
    kind, need = rule
    if kind == "never":
        return f"{info['model']}@{effort} is never chosen: {need}", info
    assert isinstance(need, frozenset)
    if kind == "all" and not need <= set(tags):
        return f"{info['model']}@{effort} needs all of: {', '.join(sorted(need))}", info
    if kind == "any" and not need & set(tags):
        return f"{info['model']}@{effort} needs one of: {', '.join(sorted(need))}", info
    return None, info


def ledger_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "goh" / "agent-spawns.jsonl"


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


def on_spawn(event: dict[str, Any]) -> dict[str, Any] | None:
    """The PreToolUse output for an Agent/Task call, or None to let it run untouched."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return None  # not a shape this hook knows: unjudged, never blocked
    agent_type = tool_input.get("subagent_type") or "general-purpose"
    model, effort = (
        v.strip() if isinstance(v, str) else ""
        for v in (tool_input.get("model"), tool_input.get("effort"))
    )
    exempt = agent_type in MODEL_EXEMPT
    info: dict[str, Any] = {"model": family(model), "effort": effort or None, "tags": []}
    if not exempt:
        why, info = judge_choice(model, effort, prompt)
        if why:
            reason = (
                f"Refused: this {agent_type} spawn -- {why}. Name model AND effort, and declare "
                f"them in the prompt as {FORMAT}, tags from this table:\n{table_text()}"
            )
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                           "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}  # fmt: skip
    out: dict[str, Any] = {}
    final = prompt
    if not exempt and CONTRACT not in prompt:
        final = prompt.rstrip() + "\n\n" + CONTRACT
        out["hookSpecificOutput"] = {
            "hookEventName": "PreToolUse",
            "updatedInput": dict(tool_input, prompt=final),
        }
    warning = _append({
        "event": "spawn", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": event.get("tool_use_id"), "model": info["model"], "model_id": model or None,
        "effort": info["effort"], "tags": info["tags"], "subagent_type": agent_type,
        "exempt": exempt, "description": tool_input.get("description"),
        "prompt_sha": hashlib.sha256(final.encode()).hexdigest(), "first_line": _first_line(prompt),
    })  # fmt: skip
    if warning:
        out["systemMessage"] = warning
    return out or None


def _usage(transcript: Path) -> dict[str, int] | None:
    """Tokens the agent spent: usage summed over its assistant messages, each message ONCE (a
    message streamed as several transcript entries repeats its usage on every entry)."""
    try:
        text = transcript.read_text(encoding="utf-8")
    except OSError:
        return None
    seen: dict[str, dict[str, Any]] = {}
    for raw in text.splitlines():
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry, dict) else None
        if isinstance(msg, dict) and isinstance(msg.get("usage"), dict):
            seen[str(msg.get("id") or len(seen))] = msg["usage"]
    keys = {"input": "input_tokens", "output": "output_tokens",
            "cache_creation": "cache_creation_input_tokens", "cache_read": "cache_read_input_tokens"}  # fmt: skip
    tokens = {k: sum(int(u.get(v) or 0) for u in seen.values()) for k, v in keys.items()}
    tokens["total"] = sum(tokens.values())
    return tokens


def _status(report: str) -> str:
    m = STATUS_LINE.match(report.splitlines()[0]) if report.strip() else None
    word = m.group(1).lower() if m else ""
    return word if word in STATUSES else "unreported"


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
    return _append({
        "event": "outcome", "time": _now(), "session_id": event.get("session_id"),
        "tool_use_id": tool_use_id, "agent_id": event.get("agent_id"),
        "agent_type": event.get("agent_type"),
        "status": _status(report if isinstance(report, str) else ""),
        "tokens": _usage(transcript) if transcript is not None else None,
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
