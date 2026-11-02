"""The backstop's log says WHEN, WHO, WHAT and WHY it killed (GOH.REAPLOG, 2026-11-02).

`~/Library/Logs/goh-load-reap.log` carried `load-reap: killed 43333; removed 0 registry file(s)`:
no time, no command, no rule. While chasing GOH.FLAKE1 -- a fixture the minute job killed under a
running test -- there was no way to match a backstop kill to the moment a test or a gate lost its
process. Every kill line now carries four fields:

  * an ISO-8601 UTC timestamp,
  * the pid,
  * the process's command line, truncated,
  * the rule that killed it: `registry`, `house-shape` or `hand-rolled-shape`.

A pass that kills nothing writes nothing (it ran every 60 s), and the file is bounded: a pass that
finds the log past its cap moves it to `<log>.1`, so a log is at most two caps' worth.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import load_kit
import pytest
import timing_bounds
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

import load_reap
import load_reap_log
import load_registry as reg

REAPER = REPO_ROOT / "tools" / "load_reap.py"

# Same worker as every other file that sweeps or orphans (tests/test_load_burning.py pins it).
pytestmark = pytest.mark.xdist_group("load_orphans")

ISO_UTC = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
GONE_OWNER_START = "Tue Jan  1 00:00:00 1990"
MARKER = "reaplog-fixture-marker"


def _job(directory: Path, log: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    """The minute job, as launchd runs it: stdout and stderr land in the log file."""
    with log.open("a", encoding="utf-8") as sink:
        return subprocess.run(
            [sys.executable, str(REAPER), "--dir", str(directory), "--log-path", str(log), *extra],
            stdout=sink,
            stderr=sink,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=timing_bounds.patience(60),
            check=False,
        )


def test_a_kill_line_carries_timestamp_pid_command_and_rule(tmp_path: Path) -> None:
    """Planted orphan, real job: the line it leaves names all four."""
    pid = load_kit.orphan(["/bin/sh", "-c", f"sleep 300; : {MARKER}"])
    assert reg.pid_ppid(pid) == 1, "the fixture did not become an orphan"
    path = reg.registry_path(tmp_path)
    reg.write(
        path,
        {
            "version": reg.VERSION,
            "owner_pid": os.getpid(),
            "owner_start": GONE_OWNER_START,
            "started_at": 0,
            "command": ["fixture"],
            "pids": [{"pid": pid, "start": reg.pid_start(pid) or ""}],
        },
    )
    log = tmp_path / "reap.log"
    result = _job(tmp_path, log)
    assert result.returncode == 0
    assert not load_kit.alive(pid), "the orphan outlived the backstop"
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if f"pid={pid}" in ln]
    assert len(lines) == 1, log.read_text(encoding="utf-8")
    line = lines[0]
    assert re.match(rf"^{ISO_UTC} ", line), line
    assert re.search(rf"\bpid={pid}\b", line), line
    assert MARKER in line, line
    assert "rule=registry" in line, line


def test_the_house_shape_sweep_names_its_rule_and_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    me, alive = os.getpid(), reg.pid_alive
    fifo = tmp_path / f"release-{me}-0.fifo"
    os.mkfifo(fifo)
    pid = load_kit.orphan(["/bin/sh", "-c", reg.spinner_command(fifo)])
    monkeypatch.setattr(reg, "pid_alive", lambda p: p != me and alive(p))
    report = load_reap.reap_orphans()
    mine = [k for k in report.kills if k.pid == pid]
    assert mine, report
    assert mine[0].rule == load_reap.RULE_HOUSE == "house-shape"
    assert str(fifo) in mine[0].command


def test_the_hand_rolled_sweep_names_its_rule_and_the_command(tmp_path: Path) -> None:
    script = tmp_path / "spin.sh"
    script.write_text("#!/bin/sh\nulimit -t 30\nwhile :; do :; done\n", encoding="utf-8")
    script.chmod(0o755)
    pid = load_kit.orphan(["/bin/sh", str(script)])
    assert load_kit.wait_until(lambda: load_reap._table().get(pid, (0, 0.0, ""))[1] >= 0.3)
    report = load_reap.reap_burning_orphans(min_cpu_s=0.3)
    mine = [k for k in report.kills if k.pid == pid]
    assert mine, report
    assert mine[0].rule == load_reap.RULE_HAND == "hand-rolled-shape"
    assert str(script) in mine[0].command


def test_a_kill_line_is_one_stamped_line_with_a_truncated_command() -> None:
    long = "/bin/sh -c " + "x" * 5000 + "\nsecond line"
    kill = load_reap.Kill(pid=4242, rule=load_reap.RULE_REGISTRY, command=long)
    line = load_reap_log.kill_line(kill)
    assert "\n" not in line
    assert re.match(rf"^{ISO_UTC} .*pid=4242.*rule=registry", line), line
    assert len(line) < 400, len(line)
    assert line.endswith("..."), line


def test_a_pass_that_kills_nothing_writes_nothing(tmp_path: Path) -> None:
    """Sixty seconds, forever: a quiet pass must not grow the log."""
    empty = tmp_path / "no-registry"
    empty.mkdir()
    log = tmp_path / "reap.log"
    for _ in range(3):
        assert _job(empty, log).returncode == 0
    assert log.read_text(encoding="utf-8") == ""


def test_a_log_past_its_cap_is_moved_aside_and_stays_bounded(tmp_path: Path) -> None:
    empty = tmp_path / "no-registry"
    empty.mkdir()
    log = tmp_path / "reap.log"
    log.write_text("old line\n" * 100, encoding="utf-8")
    assert _job(empty, log, "--max-log-bytes", "200").returncode == 0
    old = Path(f"{log}.1")
    assert old.read_text(encoding="utf-8").startswith("old line"), "the old log was not kept"
    assert not log.exists() or log.stat().st_size <= 200
    # And a log under its cap is left in place.
    log.write_text("small\n", encoding="utf-8")
    assert _job(empty, log, "--max-log-bytes", "200").returncode == 0
    assert log.read_text(encoding="utf-8") == "small\n"
