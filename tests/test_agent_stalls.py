"""`goh agent-stalls` / hooks/claude/agent_stalls.py: a subagent idle on work that never notified.

Every transcript here is FAKE, built in tmp_path in the shape Claude Code 2.1.317 writes (probed on
2026-11-08, GOH.STALLAGENT). The clock is a seam (`now`), the process table is a seam (`holders`);
the one test of the real `lsof` path holds the output file with a `sleep` THIS test starts and
reaps. No test reads the owner's transcripts or signals any process it did not start.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env, run_goh

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import agent_stalls as st

NOW = 1_800_000_000.0
SCRIPT = REPO_ROOT / "hooks" / "claude" / "agent_stalls.py"


def _assistant(text: str, stop: str = "end_turn") -> dict:
    return {
        "type": "assistant",
        "message": {"stop_reason": stop, "content": [{"type": "text", "text": text}]},
    }


def _launch(job: str, out: Path, moved: bool = False) -> dict:
    how = (
        f"Command was moved to the background (ID: {job}) after running for 180s."
        if moved
        else f"Command running in background with ID: {job}."
    )
    body = f"{how} Output is being written to: {out}. You will be notified when it completes."
    return {"type": "user", "message": {"content": [{"type": "tool_result", "content": body}]}}


def _notified(job: str) -> dict:
    prompt = f"<task-notification>\n<task-id>{job}</task-id>\n<status>completed</status>"
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt}}


def _agent(
    d: Path, agent: str, recs: list[dict], idle_min: float, meta: dict | None = None
) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    t = d / f"agent-{agent}.jsonl"
    t.write_text("".join(json.dumps(r) + "\n" for r in recs))
    (d / f"agent-{agent}.meta.json").write_text(json.dumps(meta or {"description": f"Q.{agent}"}))
    os.utime(t, (NOW - idle_min * 60, NOW - idle_min * 60))
    return t


def _none(_paths: list[str]) -> dict[str, list[int]]:
    return {}


def _waiting(d: Path, idle_min: float = 30, **kw) -> Path:
    """The KB.LIGHT2 shape: two jobs launched, one notified, then the turn ended waiting."""
    recs = [
        {"type": "user", "message": {"content": "Queue item Q.X"}},
        _launch("bone", d / "bone.output"),
        _launch("btwo", d / "btwo.output", moved=True),
        _notified("bone"),
        _assistant("I'll wait for the completion notification."),
    ]
    return _agent(d, "x1", recs, idle_min, **kw)


def test_an_agent_idle_past_the_threshold_while_waiting_is_a_stall(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _waiting(d)
    (s,) = st.stalls([d], NOW, holders=_none)
    assert (s.agent_id, s.description, s.state) == ("x1", "Q.x1", "waiting")
    assert s.last_said == "I'll wait for the completion notification."
    assert [j.id for j in s.jobs] == ["btwo"]  # the notified job is not pending
    assert s.jobs[0].output == str(d / "btwo.output")
    assert "idle 30m" in s.line() and "btwo gone" in s.line()


def test_a_job_whose_output_is_held_is_named_alive_with_its_pids(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _waiting(d)
    out = str(d / "btwo.output")
    (s,) = st.stalls([d], NOW, holders=lambda paths: {out: [42, 7]} if out in paths else {})
    assert s.jobs[0].pids == [7, 42]
    assert "btwo alive (pid 7,42)" in s.line()


@pytest.mark.parametrize(
    ("idle_min", "meta"),
    [
        (19, None),  # under the threshold: still working, as far as anyone can tell
        (30, {"description": "Q", "finished": True}),  # it returned
        (60 * 25, None),  # older than max-age: a dead session's residue, not a stall
    ],
    ids=["fresh", "finished", "residue"],
)
def test_not_a_stall(tmp_path: Path, idle_min: float, meta: dict | None) -> None:
    d = tmp_path / "subagents"
    _waiting(d, idle_min, meta=meta)
    assert st.stalls([d], NOW, holders=_none) == []


def test_an_agent_the_owner_interrupted_is_not_a_stall(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    stop = {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}}  # fmt: skip
    _agent(d, "i1", [_assistant("working", "tool_use"), stop], 120)
    assert st.stalls([d], NOW, holders=_none) == []


def test_a_hung_tool_call_is_a_stall_named_as_one(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _agent(d, "h1", [_assistant("Running the suite.", "tool_use")], 45)
    (s,) = st.stalls([d], NOW, holders=_none)
    assert s.state == "in a tool call" and s.jobs == []


def test_a_launch_phrase_in_the_agents_own_command_is_not_a_launch(tmp_path: Path) -> None:
    """Only a tool_result announces a job: an agent grepping for the phrase launched nothing."""
    cmd = "grep 'running in background with ID: bfake. Output is being written to: /x/bfake.output'"
    use = {"type": "assistant", "message": {"stop_reason": "end_turn", "content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": cmd}}]}}  # fmt: skip
    assert st.pending_jobs(json.dumps(use) + "\n") == []


def test_the_cli_exits_1_on_a_stall_and_0_without_one(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _waiting(d)
    args = [sys.executable, str(SCRIPT), "--subagents", str(d), "--now", str(NOW)]
    red = subprocess.run(args, capture_output=True, text=True, timeout=30, env=hermetic_env(), check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert red.returncode == 1, red.stderr
    assert "Q.x1 [x1] idle 30m, waiting" in red.stdout and "btwo gone" in red.stdout
    later = [*args[:-1], str(NOW - 20 * 60)]  # the same agent, seen 20 minutes earlier
    green = subprocess.run(later, capture_output=True, text=True, timeout=30, env=hermetic_env(), check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert green.returncode == 0, green.stdout
    assert green.stdout.startswith("✓ no subagent idle 20m+")


def test_the_hook_puts_a_fake_stall_in_front_of_the_coordinator(tmp_path: Path) -> None:
    """The coordinator's UserPromptSubmit event names ITS transcript; its subagents are beside it."""
    session = tmp_path / "proj" / "sess"
    _waiting(session / "subagents", idle_min=48)
    event = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "sess",
                        "transcript_path": str(session.with_suffix(".jsonl")), "prompt": "hi"})  # fmt: skip
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--hook", "--now", str(NOW)], input=event,
        capture_output=True, text=True, timeout=30, env=hermetic_env(), check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "Q.x1 [x1] idle 48m, waiting" in ctx and "btwo gone" in ctx
    assert out["systemMessage"] == ctx


@pytest.mark.parametrize("stdin", ["{}", "not json", '{"transcript_path": "/nonexistent/s.jsonl"}'])
def test_the_hook_is_silent_and_never_blocks_on_what_it_cannot_read(stdin: str) -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--hook"], input=stdin, capture_output=True, text=True,
        timeout=30, env=hermetic_env(), check=False,
    )  # fmt: skip
    assert (r.returncode, r.stdout) == (0, "")


def test_the_real_process_table_sees_a_holder_and_then_its_absence(tmp_path: Path) -> None:
    """Calibrates lsof_holders both ways on a process THIS test owns (bounded, reaped)."""
    out = tmp_path / "bown.output"
    # The shell holds the file from its first line on, says so, then becomes the sleep (same pid):
    # only the child holds it, on stdout and stderr, as a background job's shell does.
    hold = 'exec 2>"$0"; echo ready; exec sleep 30 >&2'
    argv = ["/bin/sh", "-c", hold, str(out)]
    with subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True
    ) as child:
        ready = child.stdout.readline() if child.stdout else ""
        held = st.lsof_holders([str(out)]).get(str(out))
        child.kill()
    assert ready == "ready\n"
    assert held == [child.pid]
    assert st.lsof_holders([str(out)]) == {}


def test_goh_agent_stalls_runs_this_file_and_passes_its_exit_code(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _waiting(d)
    r = run_goh(
        tmp_path, "agent-stalls", "--subagents", str(d), "--now", str(NOW), env=hermetic_env()
    )
    assert r.returncode == 1, r.stderr
    assert "Q.x1 [x1] idle 30m, waiting" in r.stdout


def test_goh_agent_stalls_names_an_interpreter_it_cannot_run(tmp_path: Path) -> None:
    gone = str(tmp_path / "no-python")
    r = run_goh(tmp_path, "agent-stalls", env=hermetic_env(GOH_PY=gone, GOH_PY_OK=gone))
    assert r.returncode == 2 and "cannot run" in r.stderr
