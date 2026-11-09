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
        (60 * 25, None),  # older than max-age: a dead session's residue, not a stall
    ],
    ids=["fresh", "residue"],
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


# --- GOH.STALLNOTIFY: the idle coordinator. Probed 2026-11-08 on Claude Code 2.1.317 (stream-json,
# the desktop app's mode): a task-notification (shell job or subagent) and a ScheduleWakeup each
# fire the coordinator's UserPromptSubmit with that text as `prompt`. The two prompts below are the
# shapes the probe logged; the hook must not care which kind of prompt woke it.
TASK_PROMPT = (
    "<task-notification>\n<task-id>a59402c9a73cb0fc7</task-id>\n<tool-use-id>toolu_0</tool-use-id>"
    "\n<status>completed</status>\n</task-notification>"
)


@pytest.mark.parametrize("prompt", [TASK_PROMPT, "WOKE"], ids=["task-notification", "wakeup"])
def test_the_hook_names_a_stall_on_a_wake_that_is_not_the_owner_typing(
    tmp_path: Path, prompt: str
) -> None:
    session = tmp_path / "proj" / "sess"
    _waiting(session / "subagents", idle_min=48)
    event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
             "transcript_path": str(session.with_suffix(".jsonl"))}  # fmt: skip
    out = json.loads(st.hook(json.dumps(event), NOW, holders=_none) or "{}")
    assert "Q.x1 [x1] idle 48m, waiting" in out["hookSpecificOutput"]["additionalContext"]


def _gone_and_alive(d: Path) -> None:
    _waiting(d)  # x1: its one pending job, btwo, is gone
    _agent(d, "x2", [_launch("bthree", d / "bthree.output"), _assistant("waiting")], 40)
    _agent(d, "x3", [_assistant("Running the suite.", "tool_use")], 45)  # no job: a hung call


def test_a_dead_wait_is_a_stall_whose_every_pending_job_is_gone(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _gone_and_alive(d)
    alive = str(d / "bthree.output")
    found = st.stalls([d], NOW, holders=lambda ps: {alive: [9]} if alive in ps else {})
    assert sorted(s.agent_id for s in found) == ["x1", "x2", "x3"]
    assert [s.agent_id for s in st.dead_waits(found)] == ["x1"]


def test_notify_tells_the_owner_once_then_again_when_the_stall_is_a_new_one(tmp_path: Path) -> None:
    d, state = tmp_path / "subagents", tmp_path / "state" / "told.json"
    t = _waiting(d)
    told: list[tuple[str, str]] = []

    def post(title: str, body: str) -> None:
        told.append((title, body))

    found = st.stalls([d], NOW, holders=_none)
    assert [s.agent_id for s in st.notify_new(found, NOW, state, post)] == ["x1"]
    assert told[0][0] == "Subagent stalled 30m" and "btwo gone" in told[0][1]
    later = NOW + 300  # the next pass: the same stall, five minutes staler -- not told again
    assert st.notify_new(st.stalls([d], later, holders=_none), later, state, post) == []
    assert len(told) == 1
    os.utime(t, (NOW + 60, NOW + 60))  # it moved, then stalled again: a new stall
    again = NOW + 60 + 25 * 60
    assert len(st.notify_new(st.stalls([d], again, holders=_none), again, state, post)) == 1
    assert len(told) == 2


def test_notify_state_holds_only_the_latest_pass(tmp_path: Path) -> None:
    d, state = tmp_path / "subagents", tmp_path / "told.json"
    _waiting(d)
    st.notify_new(st.stalls([d], NOW, holders=_none), NOW, state, lambda _t, _b: None)
    assert list(json.loads(state.read_text())) == ["x1"]
    st.notify_new([], NOW, state, lambda _t, _b: None)  # it finished: its entry goes
    assert json.loads(state.read_text()) == {}


def test_the_notify_pass_posts_through_osascript_argv_and_logs_a_stamped_line(
    tmp_path: Path,
) -> None:
    """The CLI pass end to end, with a fake `osascript` first on PATH recording its argv."""
    d, fake = tmp_path / "subagents", tmp_path / "bin"
    _waiting(d)
    fake.mkdir()
    rec = tmp_path / "argv"
    (fake / "osascript").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{rec}"\n')
    (fake / "osascript").chmod(0o755)
    env = hermetic_env(PATH=f"{fake}:/usr/bin:/bin:/usr/sbin")
    args = [sys.executable, str(SCRIPT), "--notify", "--subagents", str(d), "--now", str(NOW),
            "--state", str(tmp_path / "told.json"), "--log-path", str(tmp_path / "x.log")]  # fmt: skip
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert " agent-stalls: notified Q.x1 [x1] idle 30m" in r.stdout
    argv = rec.read_text().splitlines()
    assert argv[-2:][0] == "Subagent stalled 30m" and "btwo gone" in argv[-1]
    again = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert (again.returncode, again.stdout) == (0, "")  # told once


def _finished(
    d: Path, said: str, recs_before: list[dict] | None = None, idle_min: float = 30
) -> Path:
    """A STOPPED agent (`finished` is set on every stop): job bone launched, then the turn ended."""
    recs = [
        {"type": "user", "message": {"content": "Queue item Q.X"}},
        *(recs_before if recs_before is not None else [_launch("bone", d / "bone.output")]),
        _assistant(said),
    ]
    return _agent(d, "f1", recs, idle_min, meta={"description": "Q.f1", "finished": True})


def test_a_finished_agent_that_stopped_to_wait_on_a_dead_job_is_a_stall(tmp_path: Path) -> None:
    """`finished` is set per STOP, so an agent that ended its turn to wait on a background job
    reads finished while it waits; when that job dies no notification comes (GOH.STALLS2)."""
    d = tmp_path / "subagents"
    _finished(d, "I'll wait for the completion notification.")
    (s,) = st.stalls([d], NOW, holders=_none)
    assert (s.agent_id, s.state) == ("f1", "waiting")
    assert [j.id for j in s.jobs] == ["bone"] and not s.jobs[0].pids
    assert [x.agent_id for x in st.dead_waits([s])] == ["f1"]  # the owner's notice sees it too


@pytest.mark.parametrize(
    ("said", "recs"),
    [
        ("Status: landed\nthe job is still running but my work is done", None),  # its report
        ("Done; nothing left to wait on.", []),  # finished, no job pending
        ("Done.", [_launch("bone", "/x/bone.output"), _notified("bone")]),  # the job reported back
    ],
    ids=["report-contract", "no-job", "job-notified"],
)
def test_a_finished_agent_that_is_done_is_not_a_stall(
    tmp_path: Path, said: str, recs: list[dict] | None
) -> None:
    d = tmp_path / "subagents"
    _finished(d, said, recs)
    assert st.stalls([d], NOW, holders=_none) == []


@pytest.mark.parametrize(
    ("finished", "said", "jobs", "want"),
    [
        (True, "I'll wait for it.", True, True),  # stopped on a pending job
        (True, "Will resume when the build finishes.", False, True),  # a wait with no job seen
        (True, "Status: landed", True, False),  # a report is final
        (True, "All done.", False, False),
        (False, "I'll wait for it.", True, False),  # not stopped: running, not waiting
    ],
)
def test_is_waiting_is_the_one_definition_of_a_stop_that_is_not_the_end(
    finished: bool, said: str, jobs: bool, want: bool
) -> None:
    pend = [st.Job("b", "/x/b.output")] if jobs else []
    assert st.is_waiting(finished, said, pend) is want


def test_a_full_report_is_read_whole_not_from_the_display_truncation(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    _finished(d, "x" * 400 + "\nStatus: landed")
    assert st.full_said(next(d.glob("agent-*.jsonl"))).endswith("Status: landed")
    assert st.stalls([d], NOW, holders=_none) == []


RETIRED_JOBS = {"com.ztomer.goh-agent-stalls": "retired into com.ztomer.loopctl-tick (LOOP.CTL2)"}


@pytest.mark.parametrize("label", RETIRED_JOBS)
def test_a_retired_job_has_no_plist_no_installer_and_cannot_be_installed(
    tmp_path: Path, label: str
) -> None:
    """Nothing reinstalls a retired job: its plist and caller scripts are gone, and the installer
    refuses it for want of a plist (a `remove` still works, so a loaded copy can be taken down)."""
    assert not (REPO_ROOT / "launchagents" / f"{label}.plist").exists()
    callers = [p.name for p in (REPO_ROOT / "tools").glob("install_*.sh")
               if label in p.read_text()]  # fmt: skip
    assert callers == []
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LAUNCHAGENTS_DIR": str(tmp_path / "LA")}
    r = subprocess.run(["/bin/bash", str(REPO_ROOT / "tools" / "install_launchagent.sh"), label,
                        "install"], capture_output=True, text=True, env=env, check=False,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    assert r.returncode != 0 and not (tmp_path / "LA" / f"{label}.plist").exists()
    assert "plist" in r.stderr


# --- the one stopped-agent rule (LOOP.LINGERSTOP) ---------------------------------------------
# Probed 2026-11-08 on the coordinator's own session (Claude Code 2.1.317): TaskStop of an agent
# that had already ended its turn writes NOTHING to the agent's transcript, meta or task output
# file (a3804c28b15885a20, SRV.ARRLIVE: transcript last written 02:30:34, stopped 02:31:26); the
# only record is the TaskStop tool_result in the session transcript that stopped it. A stop
# mid-turn also appends `[Request interrupted by user]` (a921d818bcfc3397e, RT.WEBKITFLAKE).


def _session(tmp_path: Path) -> tuple[Path, Path]:
    """(subagents dir, the session transcript beside it), the layout Claude Code writes."""
    sess = tmp_path / "proj" / "s1"
    return sess / "subagents", sess.with_suffix(".jsonl")


def _task_stop(agent: str, found: bool = True) -> dict:
    if found:
        res: object = {"message": f"Successfully stopped task: {agent} (Q.{agent})",
                       "task_id": agent, "task_type": "local_agent"}  # fmt: skip
        body = json.dumps(res)
    else:
        res = f"Error: No task found with ID: {agent}. Running background agents: aother (Q.O)"
        body = f"<tool_use_error>No task found with ID: {agent}.</tool_use_error>"
    return {
        "type": "user", "timestamp": "2026-11-09T02:31:26.311Z", "toolUseResult": res,
        "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": body}]},
    }  # fmt: skip


def _parent(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


def _arrlive(d: Path) -> Path:
    """The a3804c28b15885a20 shape: a report, then a postscript, a job never notified, finished."""
    recs = [
        _launch("bk26", d / "bk26.output"),
        _assistant("Status: landed\nCommits: 40ab51b1"),
        _assistant("That notification was only the helper; my report above stands."),
    ]
    return _agent(d, "a38", recs, 105, meta={"description": "SRV.ARRLIVE", "finished": True})


@pytest.mark.parametrize("found", [True, False], ids=["stopped", "no-such-task"])
def test_an_agent_a_task_stop_named_is_stopped_not_a_stall(tmp_path: Path, found: bool) -> None:
    d, parent = _session(tmp_path)
    t = _arrlive(d)
    assert [s.agent_id for s in st.stalls([d], NOW, holders=_none)] == ["a38"]  # no marker yet
    _parent(parent, [{"type": "user", "message": {"content": "go"}}, _task_stop("a38", found)])
    assert st.stop_marker(t) == ("TaskStop" if found else "TaskStop: no such task")
    assert st.stalls([d], NOW, holders=_none) == []


def test_a_task_stop_by_a_sibling_subagent_counts(tmp_path: Path) -> None:
    d, _ = _session(tmp_path)
    t = _arrlive(d)
    _agent(d, "boss", [_task_stop("a38")], 1)
    assert st.stop_marker(t) == "TaskStop"


def test_a_stop_mid_turn_is_marked_by_its_interrupt_record(tmp_path: Path) -> None:
    d, _ = _session(tmp_path)
    stop = {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user]"}]}}  # fmt: skip
    t = _agent(d, "a92", [_assistant("working", "tool_use"), stop], 120)
    assert st.stop_marker(t) == "interrupted"


def test_the_stop_phrase_quoted_in_a_prompt_is_not_a_stop(tmp_path: Path) -> None:
    """Only a TaskStop tool_result stops an agent; a coordinator quoting its words stops nothing."""
    d, parent = _session(tmp_path)
    t = _arrlive(d)
    said = "Successfully stopped task: a38 (Q.a38) and No task found with ID: a38"
    _parent(parent, [{"type": "user", "message": {"content": said}}, _assistant(said)])
    assert st.stop_marker(t) is None
    assert [s.agent_id for s in st.stalls([d], NOW, holders=_none)] == ["a38"]


def test_a_stop_of_another_agent_is_not_this_ones(tmp_path: Path) -> None:
    d, parent = _session(tmp_path)
    t = _arrlive(d)
    _parent(parent, [_task_stop("a3")])  # a prefix of a38: ids are whole words
    assert st.stop_marker(t) is None


def test_a_transcript_that_just_ends_with_no_marker_is_unknown_never_alive(tmp_path: Path) -> None:
    """A transcript whose last record is a tool result reads neither waiting nor in a tool call:
    nothing says whether the agent lives, so it is named unknown, not mid-turn."""
    d, _ = _session(tmp_path)
    _agent(d, "u1", [_assistant("Running it.", "tool_use"), _launch("bx", d / "bx.output")], 40)
    (s,) = st.stalls([d], NOW, holders=_none)
    assert s.state == "unknown"
    assert "unknown" in s.line() and "mid-turn" not in s.line()


def test_a_live_agent_in_a_tool_call_with_no_marker_is_still_a_stall(tmp_path: Path) -> None:
    d, parent = _session(tmp_path)
    _parent(parent, [_task_stop("other")])
    _agent(d, "h2", [_assistant("Running the suite.", "tool_use")], 45)
    (s,) = st.stalls([d], NOW, holders=_none)
    assert (s.agent_id, s.state) == ("h2", "in a tool call")
