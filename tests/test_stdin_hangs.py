"""`goh agent-stalls` names a Bash-tool reader blocked on the harness's stdin (GOH.STDINHANG).

THE CASE (2026-11-15). An agent's `cat > /tmp/x.py`, beside a heredoc, read the harness's stdin
(a unix socket that never closes) and hung 5h51m after its agent had finished; its task stayed
"running" until the coordinator found the `cat` and killed it. hooks/claude/stdin_hangs.py names
such a reader -- a descendant of a Bash-tool shell whose argv is a stdin reader, with no CPU, whose
fd 0 is the shell's own harness socket -- once its agent has finished or past 2 h, with "kill PID".

The process table and fd 0 are seams; the one real-process test starts its own `sh` under a
socketpair (this test process standing in for the harness) and reaps it. Nothing here signals a
process it did not start.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
import stdin_hangs as sh

CLAUDE = "/Applications/Claude Code/2.1.325/claude.app/Contents/MacOS/claude --output-format x"
SHELL = (
    "/bin/zsh -c source /var/tmp/shell-snapshots/snapshot-zsh-1.sh 2>/dev/null || true "
    "&& eval 'sed -i x; cat > /tmp/x.py 2>/dev/null; python3 - $S <<'\"'\"'EOF'\"'\"'\\012print(1)"
    "\\012EOF' && pwd -P >| /tmp/claude-ab12-cwd"
)
HUNG_COMMAND = "sed -i x; cat > /tmp/x.py 2>/dev/null; python3 - $S <<'EOF'\nprint(1)\nEOF"
SOCK = "unix:0xb1b9e8c5fe3bd203"


def _table(cat_age: float = 6 * 3600, cat_cpu: float = 0.01, cat_cmd: str = "cat"):
    return [
        sh.Proc(100, 1, 9000, 50, CLAUDE),
        sh.Proc(200, 100, cat_age + 1, 0.01, SHELL),
        sh.Proc(300, 200, cat_age, cat_cpu, cat_cmd),
        sh.Proc(400, 1, 9000, 0, "cat"),  # a reader under no harness shell: not ours
    ]


def _fd0(**over: str):
    ids = {200: SOCK, 300: SOCK, 400: SOCK} | {int(k[1:]): v for k, v in over.items()}
    return lambda pids: {p: ids[p] for p in pids if p in ids}


def test_a_reader_on_the_harness_socket_past_2h_is_named_with_kill() -> None:
    found = sh.hangs(_table(), stdin=_fd0())
    assert [(h.pid, h.shell, h.reader) for h in found] == [(300, 200, "cat")]
    line = found[0].line()
    assert line.startswith("kill 300: `cat`") and "6h00m" in line and "past 2h" in line, line


def test_a_reader_whose_agent_finished_is_named_before_2h() -> None:
    found = sh.hangs(_table(cat_age=600), stdin=_fd0(), ended=lambda _c: "sys_updater [a1]")
    assert [h.pid for h in found] == [300]
    assert "its agent sys_updater [a1] has finished" in found[0].line()


@pytest.mark.parametrize(
    ("table", "fd0", "why"),
    [
        (_table(cat_age=600), _fd0(), "young, its agent still running"),
        (_table(cat_cpu=40.0), _fd0(), "it is working, not blocked"),
        (_table(cat_cmd="cat /etc/hosts"), _fd0(), "a file operand: not a reader"),
        (_table(cat_cmd="sleep 30000"), _fd0(), "not a reader"),
        (_table(), _fd0(p300="PIPE:0x1"), "somebody pipes into it"),
        (_table(), _fd0(p200="CHR:0x3000002", p300="CHR:0x3000002"), "/dev/null: it ends"),
    ],
)
def test_not_a_hang(table, fd0, why: str) -> None:
    assert sh.hangs(table, stdin=fd0) == [], why


def test_the_finished_agent_is_found_by_its_bash_command(tmp_path: Path) -> None:
    d = tmp_path / "subagents"
    d.mkdir()
    use = {"type": "tool_use", "name": "Bash", "input": {"command": HUNG_COMMAND}}
    rec = {"type": "assistant", "message": {"content": [use]}}
    (d / "agent-a1.jsonl").write_text(json.dumps(rec) + "\n")
    meta = d / "agent-a1.meta.json"
    meta.write_text(json.dumps({"description": "sys_updater", "finished": True}))
    assert sh.ended_agents([d])(SHELL) == "sys_updater [a1]"
    meta.write_text(json.dumps({"description": "sys_updater"}))
    assert sh.ended_agents([d])(SHELL) == "", "an agent still running has not ended"
    assert sh.ended_agents([d])(SHELL.replace("cat >", "tee")) == "", "another command"


def _await_cat_under(pid: int) -> None:
    """Until the `sh` has forked its `cat` (in a pipeline, after the other side)."""
    give_up = GiveUp(10)
    while not give_up.passed():
        if any(p.ppid == pid and p.command == "cat" for p in sh.process_table()):
            return
        time.sleep(0.05)
    raise AssertionError(f"no cat under {pid}")


@pytest.mark.load_sensitive
@pytest.mark.parametrize(("script", "named"), [("cat; :", True), ("sleep 60 | cat; :", False)])
def test_the_real_process_table_and_lsof_see_a_reader_on_its_shells_socket(script, named) -> None:
    """This test process stands in for the harness: its `sh` reads one end of a socketpair."""
    ours, theirs = socket.socketpair()
    proc = subprocess.Popen(["sh", "-c", script], stdin=theirs)
    theirs.close()
    try:
        _await_cat_under(proc.pid)
        found = sh.hangs(sh.process_table(), harness={__import__("os").getpid()}, max_hours=0)
        mine = [h for h in found if h.shell == proc.pid]
        assert bool(mine) is named, (script, found)
        if named:
            assert mine[0].reader == "cat"
    finally:
        subprocess.run(
            ["pkill", "-P", str(proc.pid)], check=False, stdin=subprocess.DEVNULL
        )  # kill-ok: only this test's sh
        proc.kill()
        proc.wait(timeout=patience(10))
        ours.close()


def _procs_file(tmp_path: Path) -> Path:
    rows = [[p.pid, p.ppid, p.age_s, p.cpu_s, p.command] for p in _table()]
    path = tmp_path / "procs.json"
    path.write_text(json.dumps({"procs": rows, "stdin": {"200": SOCK, "300": SOCK}}))
    return path


def test_the_hook_and_the_cli_name_the_hang(tmp_path: Path) -> None:
    script = REPO_ROOT / "hooks" / "claude" / "agent_stalls.py"
    session = tmp_path / "proj" / "sess"
    (session / "subagents").mkdir(parents=True)
    procs = ["--procs", str(_procs_file(tmp_path))]
    event = json.dumps({"transcript_path": str(session.with_suffix(".jsonl")), "prompt": "hi"})
    r = subprocess.run(
        [sys.executable, str(script), "--hook", *procs], input=event, capture_output=True,
        text=True, timeout=patience(30), env=hermetic_env(), check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "kill 300: `cat` (under Bash shell 200)" in ctx, ctx
    cli = subprocess.run(
        [sys.executable, str(script), "--subagents", str(session / "subagents"), *procs],
        capture_output=True, text=True, timeout=patience(30), env=hermetic_env(), check=False,
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    assert cli.returncode == 1 and "kill 300:" in cli.stdout, cli.stdout + cli.stderr


# GOH.STDINFILTERS: a `read` builtin blocks the SHELL itself -- no child process is the reader -- so
# a Bash-tool shell with no descendant, no CPU, its fd 0 still the harness socket and a `read` in
# the command it evals is named, with the shell's own pid to kill. A filter (`grep`, as ugrep shows
# in ps) blocked on the socket is named like `cat`.
READ_SHELL = SHELL.replace("cat > /tmp/x.py 2>/dev/null", "read -r answer")


def _read_table(shell_cmd: str = READ_SHELL, cpu: float = 0.01, kid: bool = False):
    rows = [sh.Proc(100, 1, 9000, 50, CLAUDE), sh.Proc(200, 100, 6 * 3600, cpu, shell_cmd)]
    return rows + ([sh.Proc(300, 200, 6 * 3600, 0.01, "sleep 99999")] if kid else [])


def test_a_shell_blocked_in_its_own_read_builtin_is_named() -> None:
    found = sh.hangs(_read_table(), stdin=_fd0())
    assert [(h.pid, h.shell, h.reader) for h in found] == [(200, 200, "read")], found
    assert found[0].line().startswith("kill 200: `read` (the Bash shell itself)"), found[0].line()


@pytest.mark.parametrize(
    ("table", "fd0", "why"),
    [
        (_read_table(SHELL.replace("cat > /tmp/x.py", "sed x")), _fd0(), "no read in its command"),
        (_read_table(cpu=40.0), _fd0(), "the shell is working"),
        (_read_table(kid=True), _fd0(), "a child runs: the shell waits on it"),
        (_read_table(), _fd0(p200="CHR:0x3000002"), "/dev/null: the read ends"),
    ],
)
def test_a_shell_not_blocked_in_read_is_not_named(table, fd0, why: str) -> None:
    assert sh.hangs(table, stdin=fd0) == [], why


def test_a_filter_on_the_harness_socket_is_named() -> None:
    ugrep = "ugrep -G --ignore-files --hidden -I --exclude-dir=.git -c pattern"
    found = sh.hangs(_table(cat_cmd=ugrep), stdin=_fd0())
    assert [(h.pid, h.reader) for h in found] == [(300, "ugrep")], found


@pytest.mark.load_sensitive
def test_the_real_process_table_sees_a_shell_blocked_in_read() -> None:
    """A real `sh` evals `read x` on one end of a socketpair: named by its own pid, no child."""
    import os

    ours, theirs = socket.socketpair()
    proc = subprocess.Popen(["sh", "-c", "eval 'read x' && pwd -P"], stdin=theirs)
    theirs.close()
    try:
        give_up, mine = GiveUp(10), []
        while not mine and not give_up.passed():
            found = sh.hangs(sh.process_table(), harness={os.getpid()}, max_hours=0)
            mine = [h for h in found if h.shell == proc.pid]
            time.sleep(0.05)
        assert [(h.pid, h.reader) for h in mine] == [(proc.pid, "read")], mine
    finally:
        proc.kill()
        proc.wait(timeout=patience(10))
        ours.close()
