"""A subagent spawn names its model, carries the report contract, and lands in the spawn ledger.

Subagents spend most of the tokens, so economy is set where a session delegates (owner ruling
2026-10-18, `~/.claude/CLAUDE.md` "Delegation is where the tokens go"). Rules 1 and 3 there were
kept by hand; `hooks/claude/agent_guard.py` keeps them structurally. As a PreToolUse hook on
`Agent` (and the legacy `Task`) it refuses a spawn naming no model, with the choice criteria in the
reason, and appends the fixed-form report contract to every prompt it allows. Each allowed spawn is
one line in `$XDG_CACHE_HOME/goh/agent-spawns.jsonl`; as a SubagentStop hook it adds the outcome
line (tokens from the agent's transcript, `Status:` from its report), so a wrong model choice in
either direction becomes visible in data rather than in a bill.

The input shapes are the ones Claude Code 2.1.292 sent, captured live: PreToolUse carries
`tool_input` and `tool_use_id`; SubagentStop carries `agent_id`, `agent_transcript_path` and
`last_assistant_message`, and the transcript's sibling `agent-<id>.meta.json` names the spawning
`toolUseId`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "agent_guard.py"
sys.path.insert(0, str(GUARD.parent))
import agent_guard
import model_choice

RULES = model_choice.load()

CONTRACT = agent_guard.CONTRACT


def _run(event: Any, cache: Path, raw: str | None = None) -> dict[str, Any] | None:
    """The hook's JSON output for `event` (None when it prints nothing). It always exits 0."""
    r = subprocess.run(
        [sys.executable, str(GUARD)],
        input=raw if raw is not None else json.dumps(event),
        capture_output=True, text=True, env=hermetic_env(XDG_CACHE_HOME=str(cache)), timeout=30,
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def _spawn(tool_input: dict[str, Any], tool_name: str = "Agent") -> dict[str, Any]:
    return {
        "session_id": "s-1", "hook_event_name": "PreToolUse", "tool_name": tool_name,
        "tool_input": tool_input, "tool_use_id": "toolu_A",
    }  # fmt: skip


def _ledger(cache: Path) -> list[dict[str, Any]]:
    path = cache / "goh" / "agent-spawns.jsonl"
    return [json.loads(ln) for ln in path.read_text().splitlines()] if path.exists() else []


BRIEF = {"description": "fix it", "prompt": "Fix the leak in foo.rs.\nRed test: t_leak.",
         "subagent_type": "general-purpose"}  # fmt: skip


# --- rule 1: a spawn names its model AND its effort ------------------------------------------


DECL = "Model-choice: sonnet@high because checkable, known-fix"
POOL = "Pool: unfit because the fix needs this repo's files and its test run"
GOOD = dict(BRIEF, model="sonnet", effort="high", prompt=f"{BRIEF['prompt']}\n{DECL}\n{POOL}")


def _reason(out: dict[str, Any] | None) -> str:
    assert out is not None, "the spawn was allowed"
    hso = out["hookSpecificOutput"]
    assert hso.get("permissionDecision") == "deny", hso
    return hso["permissionDecisionReason"]


def _allowed(out: dict[str, Any] | None) -> dict[str, Any]:
    assert out is not None
    hso = out["hookSpecificOutput"]
    assert "permissionDecision" not in hso, hso.get("permissionDecisionReason")
    return hso["updatedInput"]


@pytest.mark.parametrize(
    "drop",
    [{"model": None}, {"effort": None}, {"model": "", "effort": ""}, {"model": "  "},
     {"effort": " "}],
)  # fmt: skip
@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_a_spawn_naming_no_model_or_no_effort_is_refused_with_the_table(
    tmp_path: Path, drop: dict[str, str | None], tool_name: str
) -> None:
    ti = dict(GOOD) | {k: v for k, v in drop.items() if v is not None}
    for k in [k for k, v in drop.items() if v is None]:
        del ti[k]
    reason = _reason(_run(_spawn(ti, tool_name), tmp_path))
    missing = "model" if "model" in drop else "effort"
    assert f"names no {missing}" in reason, reason
    for needle in ("sonnet@high, opus@low", "checkable + known-fix", "opus@xhigh", "read-only",
                   "owner-asked", "never: sonnet@low, sonnet@medium, sonnet@xhigh"):  # fmt: skip
        assert needle in reason, (needle, reason)
    assert len(reason.splitlines()) <= 10, reason  # the table in a few lines
    assert _ledger(tmp_path) == []  # a refused spawn is not a spawn


@pytest.mark.parametrize("agent_type", sorted(RULES.exempt))
def test_an_exempt_agent_type_runs_without_a_choice_and_without_the_contract(
    tmp_path: Path, agent_type: str
) -> None:
    out = _run(_spawn(dict(BRIEF, subagent_type=agent_type)), tmp_path)
    assert out is None or "permissionDecision" not in out.get("hookSpecificOutput", {}), out
    assert out is None or "updatedInput" not in out.get("hookSpecificOutput", {}), out
    (line,) = _ledger(tmp_path)
    assert line["subagent_type"] == agent_type and line["exempt"] is True


def test_the_exempt_list_is_the_read_only_and_fork_types() -> None:
    assert RULES.exempt == {"Explore", "claude-code-guide", "statusline-setup", "fork"}


# --- rule 2: the declared choice ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "effort", "because"),
    [
        ("sonnet", "high", "checkable, known-fix"),
        ("opus", "low", "known-fix checkable"),
        ("opus", "medium", "unknown-root-cause"),
        ("opus", "high", "design-ui, security"),
        ("opus", "high", "research"),
        ("opus", "medium", "unchecked"),
        ("opus", "xhigh", "escalation"),
        ("haiku", "low", "read-only"),
        ("haiku", "medium", "read-only"),
        ("fable", "high", "owner-asked"),
        ("opus", "max", "owner-asked"),
        ("sonnet", "max", "owner-asked"),
        ("claude-opus-5-5", "medium", "audit-review"),  # a full id maps to its alias
        ("claude-sonnet-5-5[1m]", "high", "checkable, known-fix"),
    ],
)
def test_a_declared_choice_the_table_accepts_runs_with_the_contract(
    tmp_path: Path, model: str, effort: str, because: str
) -> None:
    alias = agent_guard.family(model, RULES)
    prompt = f"Do the thing.\nModel-choice: {alias}@{effort} because {because}\n{POOL}"
    ti = dict(BRIEF, model=model, effort=effort, prompt=prompt)
    new = _allowed(_run(_spawn(ti), tmp_path))
    routed = {"model": new["model"], "effort": new["effort"]}
    assert new == dict(ti, prompt=new["prompt"], **routed)  # only the prompt, or the tier, changes
    assert new["prompt"].rstrip().endswith(CONTRACT)
    if routed == {"model": model, "effort": effort}:
        assert new["prompt"].startswith(prompt)
    else:  # exploration moved a cheap-tier spawn to the other cheap tier (its own suite pins it)
        cheap = RULES.classes("cheap")
        assert f"{alias}@{effort}" in cheap and f"{new['model']}@{new['effort']}" in cheap


@pytest.mark.parametrize(
    ("model", "effort", "line", "needle"),
    [
        ("sonnet", "high", None, "no `Model-choice:` line"),
        ("sonnet", "high", "Model-choice: sonnet@high because checkable, easy", "unknown tag"),
        ("sonnet", "high", "Model-choice: sonnet@high because checkable", "needs all of"),
        ("opus", "low", "Model-choice: opus@low because known-fix", "needs all of"),
        ("opus", "medium", "Model-choice: opus@medium because checkable", "needs one of"),
        ("opus", "xhigh", "Model-choice: opus@xhigh because security", "needs all of"),
        ("haiku", "low", "Model-choice: haiku@low because checkable, known-fix", "needs all of"),
        ("fable", "high", "Model-choice: fable@high because research", "needs all of"),
        ("opus", "max", "Model-choice: opus@max because escalation", "needs all of"),
        ("sonnet", "high", "Model-choice: opus@high because research", "disagrees"),
        ("opus", "high", "Model-choice: opus@medium because research", "disagrees"),
        ("sonnet", "medium", "Model-choice: sonnet@medium because checkable, known-fix", "62.7%"),
        ("sonnet", "low", "Model-choice: sonnet@low because checkable, known-fix", "62.7%"),
        ("sonnet", "xhigh", "Model-choice: sonnet@xhigh because escalation", "costs more"),
        ("haiku", "high", "Model-choice: haiku@high because read-only", "not in the table"),
        ("gpt-5", "high", "Model-choice: gpt-5@high because research", "unknown model"),
        ("opus", "warm", "Model-choice: opus@warm because research", "unknown effort"),
    ],
)
def test_a_choice_the_table_does_not_accept_is_refused(
    tmp_path: Path, model: str, effort: str, line: str | None, needle: str
) -> None:
    prompt = "Do the thing." + (f"\n{line}" if line else "")
    reason = _reason(_run(_spawn(dict(BRIEF, model=model, effort=effort, prompt=prompt)), tmp_path))
    assert needle in reason, reason
    assert "Model-choice: <model>@<effort> because <tags>" in reason, reason


def test_two_declared_lines_that_disagree_are_refused(tmp_path: Path) -> None:
    prompt = f"{DECL}\nlater:\nModel-choice: opus@high because research"
    out = _run(_spawn(dict(GOOD, prompt=prompt)), tmp_path)
    assert "disagree" in _reason(out)


def test_the_same_line_twice_is_one_declaration(tmp_path: Path) -> None:
    _allowed(_run(_spawn(dict(GOOD, prompt=f"{DECL}\n...\n{DECL}\n{POOL}")), tmp_path))


# --- rule 3: the report contract ----------------------------------------------------------------


def test_the_contract_opens_with_a_machine_readable_status_line() -> None:
    assert "first line is `Status: landed|blocked|failed|answered`" in CONTRACT
    assert "at most 15 lines" in CONTRACT and "No narrative." in CONTRACT


def test_a_respawn_does_not_double_the_contract(tmp_path: Path) -> None:
    once = GOOD["prompt"] + "\n\n" + CONTRACT
    out = _run(_spawn(dict(GOOD, prompt=once)), tmp_path)
    assert out is None or "permissionDecision" not in out.get("hookSpecificOutput", {}), out
    prompt = (out or {}).get("hookSpecificOutput", {}).get("updatedInput", {}).get("prompt", once)
    assert prompt.count(CONTRACT) == 1, prompt


def test_another_tool_is_not_touched(tmp_path: Path) -> None:
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert _run(event, tmp_path) is None
    assert _ledger(tmp_path) == []


# --- the ledger ---------------------------------------------------------------------------------


def test_an_allowed_spawn_is_one_ledger_line_with_its_choice(tmp_path: Path) -> None:
    prompt = BRIEF["prompt"] + "\nModel-choice: opus@high because research"
    _run(_spawn(dict(BRIEF, model="claude-opus-5-5", effort="high", prompt=prompt)), tmp_path)
    (line,) = _ledger(tmp_path)
    assert line["event"] == "spawn"
    assert line["session_id"] == "s-1" and line["tool_use_id"] == "toolu_A"
    assert line["model"] == "opus" and line["model_id"] == "claude-opus-5-5"
    assert line["effort"] == "high" and line["tags"] == ["research"]
    assert line["declared"] == "opus@high" and line["explored"] is False
    assert line["exempt"] is False and line["description"] == "fix it"
    assert line["first_line"] == "Fix the leak in foo.rs."
    assert len(line["prompt_sha"]) == 64 and line["time"]


def test_a_ledger_that_cannot_be_written_never_blocks_the_spawn(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("a file where the cache directory should be\n")
    out = _run(_spawn(GOOD), blocker)
    assert _allowed(out)["prompt"].rstrip().endswith(CONTRACT)
    assert out is not None and "ledger" in out.get("systemMessage", ""), out


def _transcript(
    tmp_path: Path,
    agent_id: str,
    usages: list[tuple[str, dict[str, int]]],
    final: str | None = None,
) -> Path:
    """An agent transcript: the brief, assistant entries carrying `usages`, and -- when `final` is
    given -- the closing entry whose text is the agent's report (what SubagentStop waits for)."""
    sub = tmp_path / "proj" / "subagents"
    sub.mkdir(parents=True)
    lines = [{"type": "user", "message": {"role": "user", "content": "the brief"}}]
    lines += [{"type": "assistant", "message": {"id": mid, "usage": u}} for mid, u in usages]
    if final is not None:
        lines.append({"type": "assistant", "message": {
            "id": "m-final", "content": [{"type": "text", "text": final}],
            "usage": {"input_tokens": 0, "output_tokens": 0}}})  # fmt: skip
    path = sub / f"agent-{agent_id}.jsonl"
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))
    (sub / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "toolUseId": "toolu_A", "model": "sonnet"})
    )
    return path


def _stop(path: Path | None, last: str, agent_id: str = "a1") -> dict[str, Any]:
    return {
        "session_id": "s-1", "hook_event_name": "SubagentStop", "agent_id": agent_id,
        "agent_type": "general-purpose", "last_assistant_message": last,
        "agent_transcript_path": str(path) if path else None, "stop_hook_active": False,
    }  # fmt: skip


def test_a_finished_agent_records_its_tokens_and_status(tmp_path: Path) -> None:
    u1 = {"input_tokens": 10, "output_tokens": 100, "cache_creation_input_tokens": 1000,
          "cache_read_input_tokens": 5000}  # fmt: skip
    u2 = {"input_tokens": 20, "output_tokens": 200, "cache_creation_input_tokens": 2000,
          "cache_read_input_tokens": 6000}  # fmt: skip
    # A message streamed as several transcript entries carries its usage on each, growing as it
    # streams (live, 2.1.292: output 6 on the thinking entry, 166 on the text): it counts once,
    # at its largest.
    early = dict(u1, output_tokens=6)
    report = "Status: landed\nabc1234 fix the leak"
    path = _transcript(tmp_path, "a1", [("m1", early), ("m1", u1), ("m2", u2)], final=report)
    cache = tmp_path / "cache"
    assert _run(_stop(path, report), cache) is None
    (line,) = _ledger(cache)
    assert line["event"] == "outcome" and line["status"] == "landed"
    assert line["tool_use_id"] == "toolu_A" and line["agent_id"] == "a1"
    assert line["tokens"] == {"input": 30, "output": 300, "cache_creation": 3000,
                              "cache_read": 11000, "total": 14330}  # fmt: skip
    assert line["tokens_settled"] is True
    assert line["report"] == report  # the audit checks a landed report's shas against the remote


@pytest.mark.parametrize(
    ("last", "status"),
    [
        ("Status: blocked\nneeds a decision", "blocked"),
        ("  status:  Failed  \n", "failed"),
        ("Status: answered", "answered"),
        ("Here is a long narrative.\nStatus: landed", "unreported"),  # not the FIRST line
        ("Status: done", "unreported"),  # outside the closed set
        ("", "unreported"),
    ],
)
def test_status_is_read_from_the_first_line_only(tmp_path: Path, last: str, status: str) -> None:
    path = _transcript(tmp_path, "a1", [], final=last)
    _run(_stop(path, last), tmp_path / "cache")
    assert _ledger(tmp_path / "cache")[0]["status"] == status


def test_an_outcome_without_a_transcript_is_still_recorded(tmp_path: Path) -> None:
    assert _run(_stop(tmp_path / "gone.jsonl", "Status: landed"), tmp_path) is None
    (line,) = _ledger(tmp_path)
    assert line["status"] == "landed" and line["tokens"] is None and line["tool_use_id"] is None


def test_the_outcome_waits_for_the_transcript_to_catch_up(tmp_path: Path) -> None:
    """SubagentStop fires BEFORE the agent's closing entry reaches its transcript (live, 2.1.292:
    the hook saw only the thinking entry; the text entry landed within 2 s), so a hook that reads
    at once records a fraction of the tokens. It reads until the report's own text is there."""
    import threading

    report = "Status: answered\n4"
    path = _transcript(tmp_path, "a1", [("m1", {"input_tokens": 1, "output_tokens": 5})])
    closing = {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text",
               "text": report}], "usage": {"input_tokens": 1, "output_tokens": 200}}}  # fmt: skip
    late = threading.Timer(0.3, lambda: path.open("a").write(json.dumps(closing) + "\n"))
    late.start()
    try:
        text, settled = agent_guard.settled_transcript(path, report, deadline_s=5.0)
    finally:
        late.join()
    assert settled and agent_guard.usage(text)["output"] == 200


def test_a_transcript_that_never_catches_up_is_recorded_as_unsettled(tmp_path: Path) -> None:
    path = _transcript(tmp_path, "a1", [("m1", {"input_tokens": 1, "output_tokens": 5})])
    text, settled = agent_guard.settled_transcript(path, "Status: landed", deadline_s=0.2)
    assert not settled and agent_guard.usage(text)["output"] == 5


# --- fail open ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": "a string"}',
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
                "tool_input": {"prompt": 7, "model": "sonnet"},
            }
        ),
        '{"hook_event_name": "SubagentStop", "agent_transcript_path": 12}',
        "",
    ],
)
def test_malformed_input_never_blocks_a_spawn(tmp_path: Path, raw: str) -> None:
    out = _run(None, tmp_path, raw=raw)
    assert out is None or out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


# --- rule 4: cheap and lookup work goes to the free pools first (owner ruling 2026-10-25) ------


POOL_CLASSES = RULES.classes(*RULES.data["pool_first"]["tiers"])
TAGS = {"cheap": "checkable, known-fix", "lookup": "read-only"}


def _pool_spawn(cls: str, pool_line: str | None) -> dict[str, Any]:
    alias, effort = cls.split("@")
    tags = TAGS[RULES.tiers[cls]["tier"]]
    prompt = f"Do the thing.\nModel-choice: {cls} because {tags}" + (
        f"\n{pool_line}" if pool_line is not None else ""
    )
    return dict(BRIEF, model=alias, effort=effort, prompt=prompt)


def test_pool_first_covers_the_cheap_and_lookup_classes() -> None:
    assert sorted(POOL_CLASSES) == sorted(RULES.classes("cheap", "lookup")), POOL_CLASSES


@pytest.mark.parametrize("cls", POOL_CLASSES)
@pytest.mark.parametrize("line", [None, "Pool: tried", "Pool: unfit", "Pool: unfit because ",
                                  "Pool: maybe later"])  # fmt: skip
def test_a_cheap_or_lookup_spawn_that_skipped_the_pool_is_refused_with_the_way_there(
    tmp_path: Path, cls: str, line: str | None
) -> None:
    reason = _reason(_run(_spawn(_pool_spawn(cls, line)), tmp_path))
    pool = RULES.data["pool_first"]
    assert pool["tool"] in reason, reason
    assert pool["task_class"][RULES.tiers[cls]["tier"]] in reason, reason
    for form in ("Pool: tried <", "Pool: unfit because <"):
        assert form in reason, (form, reason)
    assert _ledger(tmp_path) == []


@pytest.mark.parametrize("cls", POOL_CLASSES)
@pytest.mark.parametrize(
    ("line", "said"),
    [("Pool: tried escalate_to_caller", "tried"),
     ("- Pool: unfit because it edits this repo and runs its tests", "unfit")],
)  # fmt: skip
def test_a_spawn_that_declares_the_pool_runs_and_the_ledger_keeps_what_it_said(
    tmp_path: Path, cls: str, line: str, said: str
) -> None:
    _allowed(_run(_spawn(_pool_spawn(cls, line)), tmp_path))
    (rec,) = _ledger(tmp_path)
    assert rec["pool"]["said"] == said and rec["pool"]["why"], rec


def test_opus_tier_work_needs_no_pool_line(tmp_path: Path) -> None:
    prompt = "Find the root cause.\nModel-choice: opus@high because unknown-root-cause"
    _allowed(_run(_spawn(dict(BRIEF, model="opus", effort="high", prompt=prompt)), tmp_path))
    (rec,) = _ledger(tmp_path)
    assert rec["pool"] is None


def test_the_weekly_live_proof_prompt_passes_the_guard(tmp_path: Path) -> None:
    """The watch's live proof spawns one agent the guard must ALLOW; a rule the proof's prompt
    does not satisfy turns the weekly proof red for a reason that is not the hook's."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import model_choice_watch as watch

    prompt = watch.PROOF_PROMPT.split("this prompt, exactly:\n", 1)[1]
    ti = {"description": watch.PROOF_DESC, "prompt": prompt, "subagent_type": "general-purpose",
          "model": "haiku", "effort": "low"}  # fmt: skip
    _allowed(_run(_spawn(ti), tmp_path))


def test_the_contract_asks_for_the_faults_seen_line() -> None:
    assert "`Faults seen: <each fault you noticed but did not fix" in CONTRACT
    assert "';'-separated" in CONTRACT and "or none>`" in CONTRACT


def test_faults_none_is_an_empty_list() -> None:
    assert agent_guard.faults("Status: landed\nFaults seen: none") == []


def test_faults_several_are_split_on_semicolons() -> None:
    report = "Status: landed\nabc1234 x\nFaults seen: WARN disk on .33 health; /srv/t not g+w (.90)"
    assert agent_guard.faults(report) == ["WARN disk on .33 health", "/srv/t not g+w (.90)"]


def test_faults_are_recorded_exactly_as_written() -> None:
    """G15: a leading backtick was stripped from the first fault (only whitespace is trimmed)."""
    report = "Faults seen: `tools/land.sh` printed landed; second `one`\n"
    assert agent_guard.faults(report) == ["`tools/land.sh` printed landed", "second `one`"]
    assert agent_guard.faults("Faults seen:   `x` at y  ") == ["`x` at y"]


def test_faults_a_backticked_none_is_still_none() -> None:
    assert agent_guard.faults("Faults seen: `none`") == []


def test_faults_missing_line_is_null_not_none() -> None:
    assert agent_guard.faults("Status: landed\nall good") is None


def test_the_outcome_record_carries_faults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    agent_guard.on_stop(
        {
            "session_id": "s",
            "agent_id": "a",
            "last_assistant_message": "Status: answered\nFaults seen: red CI on x",
        }
    )
    rec = json.loads(agent_guard.ledger_path().read_text().splitlines()[-1])
    assert rec["faults"] == ["red CI on x"]
