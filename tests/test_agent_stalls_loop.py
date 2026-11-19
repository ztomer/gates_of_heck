"""The per-turn agent-stalls hook prints .loop's stale-agent lines (GOH.STALEHOOK).

`loopctl stale` (LOOP.STALECLI) owns the rule; hooks/claude/loop_stale.py asks it with the event's
projects root and agent_stalls prints its lines. The real-CLI test runs only where .loop is checked
out (never in goh's CI); the stubs pin the wiring everywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import suite_home
from conftest import REPO_ROOT, hermetic_env
from test_agent_stalls import _agent, _assistant

SCRIPT = REPO_ROOT / "hooks" / "claude" / "agent_stalls.py"

# `loopctl stale` (LOOP.STALECLI) owns the rule; the hook asks it with the event's projects root and
# prints its lines. The real-CLI test runs only where .loop is checked out (never in goh's CI).
# The REAL home's: the suite home hides the loop's checkout (suite_home.HIDDEN, GOH.CIRED01310),
# so a test that means the loop's own CLI names the real home, and hands it a queue of its own.
REAL_HOME = Path(os.environ.get(suite_home.MARKER) or suite_home.REAL_HOME)
LOOPCTL = REAL_HOME / "Projects" / ".loop" / "tools" / "loopctl.py"


def _hook_event(projects: Path) -> str:
    session = projects / "proj" / "sess"
    session.mkdir(parents=True, exist_ok=True)
    return json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "hi",
                       "transcript_path": str(session.with_suffix(".jsonl"))})  # fmt: skip


def _run_hook(event: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--hook", "--procs", os.devnull], input=event,
        capture_output=True, text=True,
        timeout=60, env=hermetic_env(**env), check=False,
    )  # fmt: skip


def test_the_hook_prints_loopctl_stale_lines_asked_with_its_projects_root(tmp_path: Path) -> None:
    said = tmp_path / "argv"
    stub = tmp_path / "loopctl.py"
    stub.write_text(
        "import sys\n"
        f"open({str(said)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
        "print('✗ X.DONE: done, but agent adone is still alive: stop it (TaskStop adone)')\n"
        "sys.exit(10)\n"
    )
    projects = tmp_path / "projects"
    r = _run_hook(_hook_event(projects), GOH_LOOPCTL=str(stub))
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "TaskStop adone" in ctx, ctx
    assert said.read_text() == f"stale --projects {projects}", said.read_text()


@pytest.mark.parametrize("body", ["import sys; sys.exit(2)", "import time; time.sleep(60)"])
def test_a_loopctl_that_fails_never_reaches_the_prompt(tmp_path: Path, body: str) -> None:
    stub = tmp_path / "loopctl.py"
    stub.write_text(body + "\n")
    r = _run_hook(_hook_event(tmp_path / "projects"), GOH_LOOPCTL=str(stub),
                  GOH_LOOPCTL_TIMEOUT_S="2")  # fmt: skip
    assert (r.returncode, r.stdout) == (0, ""), r.stdout
    assert "loopctl stale" in r.stderr, r.stderr


@pytest.mark.load_sensitive
@pytest.mark.skipif(not LOOPCTL.is_file(), reason="no .loop checkout (its CLI is what is asked)")
def test_the_real_loopctl_names_a_closed_items_agent_and_a_2h_agent(tmp_path: Path) -> None:
    import time
    from datetime import UTC, datetime

    projects = tmp_path / "projects"
    event = _hook_event(projects)
    d = projects / "proj" / "sess" / "subagents"
    begun = datetime.fromtimestamp(time.time() - 3 * 3600, UTC).isoformat()
    for aid, first in (("adone", {}), ("along", {"timestamp": begun})):
        recs = [{"type": "user", "message": {"content": "Queue item"}, **first},
                _assistant("Running the suite.", "tool_use")]  # fmt: skip
        _agent(d, aid, recs, 0)
        os.utime(d / f"agent-{aid}.jsonl")  # written just now, on the real clock loopctl reads
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"items": [
        {"id": "X.DONE", "repo": "r", "status": "done", "agent": "adone"},
        {"id": "X.LONG", "repo": "r", "status": "in_progress", "agent": "along"},
    ]}))  # fmt: skip
    r = _run_hook(event, GOH_LOOPCTL=str(LOOPCTL), LOOPCTL_QUEUE=str(queue))
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "X.DONE" in ctx and "TaskStop adone" in ctx, ctx
    assert "X.LONG" in ctx and "2 h house limit" in ctx and "TaskStop along" in ctx, ctx
