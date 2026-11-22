"""`goh land --wait`'s exit is the record's, on every path (GOH.WAITEARLY, lib/landq_look.py).

2026-11-21: three lanes in one evening read a wait as finished while the land still ran. Their
tool shell was zsh, where `${PIPESTATUS[0]}` is empty and a `cmd | tail` exits with tail's 0, and
the harness moved a 180 s call to the background, reporting the compound command's exit. The wait
itself had three paths that answered something other than the record: a signal (143, 129, or, a
background job's inherited SIG_IGN for SIGINT, no exit at all), a closed stdout (the last line's
BrokenPipeError, exit 1, read as red), and a done record in no state it knows (exit 1, red). Now
0 is landed, 1 is red, 3 is every other way out, and every exit's last line names its code, so a
reader of the output alone is not left to the shell's. A batch member's wait prints where it is
and for how long again every `heartbeat` seconds: "gate 1 of a batch of 3" for 15 min was all
GOH.DIFFCOVCARRY's wait said.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import GiveUp, patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))
import landq_look
import landq_state


def _running(q: Path, name: str = "01-T.S") -> Path:
    (q / "running").mkdir(parents=True, exist_ok=True)
    path = q / "running" / f"{name}.json"
    landq_state.write(path, {"item": "T.S", "repo": "/r", "sha": "a" * 40, "mode": "land"})
    return path


def _land(q: Path, name: str = "01-T.S", state: str = "landed") -> None:
    (q / "running" / f"{name}.json").unlink(missing_ok=True)
    (q / "done").mkdir(exist_ok=True)
    landq_state.write(q / "done" / f"{name}.json",
                      {"item": "T.S", "mode": "land", "state": state, "sha": "b" * 40})  # fmt: skip


def test_a_record_in_no_known_state_exits_3_not_red(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(landq_look, "poll", 0.01)
    _running(tmp_path)
    _land(tmp_path, state="superseded")
    assert landq_look.wait(tmp_path, "T.S", patience(30), lambda: None) == 3
    assert "exit 3" in capsys.readouterr().err


@pytest.mark.parametrize(("state", "code"), [("landed", 0), ("red", 1)])
def test_the_last_line_names_the_exit(tmp_path, monkeypatch, capsys, state, code):
    monkeypatch.setattr(landq_look, "poll", 0.01)
    _running(tmp_path)
    _land(tmp_path, state=state)
    assert landq_look.wait(tmp_path, "T.S", patience(30), lambda: None) == code
    out = capsys.readouterr()
    assert f"exit {code}" in (out.out + out.err).strip().splitlines()[-1]


@pytest.mark.load_sensitive  # a thread, its join bounded by patience
def test_a_batch_members_wait_says_its_gate_and_for_how_long_again(tmp_path, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(landq_look, "info", said.append)
    monkeypatch.setattr(landq_look, "ok", said.append)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    monkeypatch.setattr(landq_look, "heartbeat", 0.05)
    _running(tmp_path, "00-T.AHEAD")
    _running(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "00-T.AHEAD.gate1.log").write_text("")
    out: list[int] = []
    t = threading.Thread(
        target=lambda: out.append(landq_look.wait(tmp_path, "T.S", patience(60), lambda: None))
    )
    t.start()
    give_up = GiveUp(30)
    while sum("gate 1 of a batch of 2" in s for s in said) < 3:
        assert not give_up.passed(), said
        time.sleep(0.01)
    _land(tmp_path)
    t.join(patience(30))
    assert out == [0]
    gate = [s for s in said if "gate 1 of a batch of 2" in s]
    assert all(" for " in s and "waited" in s for s in gate[1:]), gate


def _spawn(q: Path, **kw) -> subprocess.Popen:
    env = hermetic_env(GOH_LANDQ_DIR=str(q), GOH_LIVE="1")
    return subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "tools" / "landq.py"), "--wait", "T.S"],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
        text=True, **kw,
    )  # fmt: skip


def _ignore_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # what a non-interactive shell's `cmd &` gets


@pytest.mark.load_sensitive  # a child process, its exit bounded by patience
@pytest.mark.parametrize(
    ("sig", "pre"),
    [
        (signal.SIGTERM, None),
        (signal.SIGINT, None),
        (signal.SIGHUP, None),
        (signal.SIGINT, _ignore_sigint),
    ],
)
def test_a_signal_ends_the_wait_with_3(tmp_path, sig, pre):
    _running(tmp_path)
    p = _spawn(tmp_path, preexec_fn=pre)
    try:
        assert p.stdout is not None
        assert "is running" in p.stdout.readline()  # the wait is in its loop
        p.send_signal(sig)
        p.wait(patience(30))
        assert p.returncode == 3, (p.returncode, p.stderr.read() if p.stderr else "")
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.load_sensitive  # a child process, its exit bounded by patience
def test_a_closed_stdout_still_exits_by_the_record(tmp_path):
    _running(tmp_path)
    p = _spawn(tmp_path)
    try:
        assert p.stdout is not None
        assert "is running" in p.stdout.readline()
        p.stdout.close()  # the reader went away: `| head -1`, a harness that stopped reading
        _land(tmp_path)
        p.wait(patience(30))
        assert p.returncode == 0, p.stderr.read() if p.stderr else ""
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.load_sensitive  # a thread waited for, bounded by patience
def test_a_wait_on_a_land_running_twenty_minutes_says_so(tmp_path, monkeypatch):
    """GOH.WAITELAPSED: each 170 s call said 'for 2:00 (waited 2:00)' whatever the land's age; the
    line names the land's own time since its request (the request name's time_ns prefix)."""
    said: list[str] = []
    monkeypatch.setattr(landq_look, "info", said.append)
    monkeypatch.setattr(landq_look, "ok", said.append)
    monkeypatch.setattr(landq_look, "poll", 0.01)
    monkeypatch.setattr(landq_look, "heartbeat", 0.05)
    name = f"0{time.time_ns() - 20 * 60 * 10**9}-T.S"
    _running(tmp_path, name)
    out: list[int] = []
    t = threading.Thread(
        target=lambda: out.append(landq_look.wait(tmp_path, "T.S", patience(60), lambda: None))
    )
    t.start()
    give_up = GiveUp(30)
    while not any("still" in s for s in said):
        assert not give_up.passed(), said
        time.sleep(0.01)
    _land(tmp_path, name)
    t.join(patience(30))
    assert out == [0]
    line = next(s for s in said if "still" in s)
    assert "requested 20:0" in line and "waited" in line, line
