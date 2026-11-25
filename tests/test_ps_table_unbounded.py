"""A read of the WHOLE process table is judged by CPU, never by a wall clock (GOH.SELFGATEPS).

THE CLASS. `load_kit.live_processes` ran `ps -ww -ax` under `timeout=30`. At load 50-150 that `ps`
is runnable but starved: the 30 s expired, the suite's last act (`suite_leak_check`, a session
fixture) raised `TimeoutExpired`, and the error landed on whichever test ran last -- 2026-11-15,
tests/test_self_gate.py and tests/test_config_schema.py, a whole land lost to a process listing.
A starved `ps` is not a hung one: only one that spends no CPU and has nothing runnable for the
stall bound is hung. The bound is `pgid_cpu.wait_for_exit`'s stall, the house's, never a wall cap.
"""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import time
from pathlib import Path

import load_kit
import pid_owner
import ps_table
import pytest

TESTS = Path(__file__).resolve().parent
# Every tests/ module's text, globbed below (lib/declared_reads.py).
TREE_READS = ("tests/*.py",)
REAL_PS = "/bin/ps"
WHOLE_TABLE = {"-ax", "-Ax", "-A", "-axww", "-Ao", "-ao", "-ww"}


def _shim(tmp_path: Path, body: str) -> Path:
    shim = tmp_path / "shim"
    shim.mkdir()
    ps = shim / "ps"
    ps.write_text(f"#!/bin/bash\n{body}\n")
    ps.chmod(ps.stat().st_mode | stat.S_IXUSR)
    return shim


def test_a_starved_ps_that_would_time_out_on_a_wall_clock_still_lists_the_table(monkeypatch):
    """The faked starvation: any `subprocess.run(ps, timeout=...)` expires, as 30 s did at 150."""
    real_run = subprocess.run

    def starved(argv, *args, **kwargs):
        if argv and Path(argv[0]).name == "ps" and kwargs.get("timeout") is not None:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", starved)
    table = load_kit.live_processes()
    assert any(pid == os.getpid() for pid, _ppid, _cmd in table), "this process is in the table"


@pytest.mark.load_sensitive
def test_a_ps_slow_in_wall_time_but_spending_cpu_is_waited_for(tmp_path, monkeypatch):
    """Slow is not hung: a `ps` that burns CPU past the stall bound still answers."""
    burn = "end=$((SECONDS+3)); while [ $SECONDS -lt $end ]; do :; done"
    shim = _shim(tmp_path, f'{burn}\nexec {REAL_PS} "$@"')
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ps_table, "PS_STALL", 1)
    t0 = time.monotonic()
    table = load_kit.live_processes()
    assert time.monotonic() - t0 > ps_table.PS_STALL, "the shim really was slow"
    assert any(pid == os.getpid() for pid, _ppid, _cmd in table)


@pytest.mark.load_sensitive
def test_a_ps_that_spends_nothing_is_hung_and_is_stopped_and_named(tmp_path, monkeypatch):
    mark = tmp_path / "pid"
    shim = _shim(tmp_path, f"echo $$ $({REAL_PS} -o lstart= -p $$) > {mark}\nexec sleep 600")
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ps_table, "PS_STALL", 1)
    with pytest.raises(RuntimeError, match=r"stalled"):
        load_kit.live_processes()
    monkeypatch.undo()  # the shim's `ps` answers nothing: ask the kernel, not it
    # stopped, not left: judged by its start time, so a reused pid is not read as it
    assert not pid_owner.alive(pid_owner.read_stamp(mark))


def _calls_with_timeout(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        argv = node.args[0]
        if not (isinstance(argv, ast.List) and argv.elts):
            continue
        words = [e.value for e in argv.elts if isinstance(e, ast.Constant)]
        if not words or words[0] != "ps" or "-p" in words:
            continue
        if not WHOLE_TABLE.intersection(words):
            continue
        if any(kw.arg == "timeout" for kw in node.keywords):
            yield node.lineno, words


def test_no_test_reads_the_whole_process_table_under_a_wall_clock_timeout():
    found = []
    for path in sorted(TESTS.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for lineno, words in _calls_with_timeout(ast.parse(path.read_text())):
            found.append(f"{path.name}:{lineno} {words}")
    assert not found, (
        "a whole-table `ps` under `timeout=` expires on a starved box, not a hung one "
        "(GOH.SELFGATEPS); read it through load_kit.live_processes: " + "; ".join(found)
    )


def test_the_structural_check_sees_a_whole_table_ps_under_a_timeout():
    code = 'import subprocess\nsubprocess.run(["ps", "-ww", "-ax", "-o", "pid="], timeout=30)\n'
    assert list(_calls_with_timeout(ast.parse(code)))
    scoped = 'import subprocess\nsubprocess.run(["ps", "-o", "stat=", "-p", "1"], timeout=10)\n'
    assert not list(_calls_with_timeout(ast.parse(scoped)))
