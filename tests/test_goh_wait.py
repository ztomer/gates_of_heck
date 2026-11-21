"""`goh wait KIND ...` (`lib/proc_wait.py`, GOH.WAITSTOP): each kind ends when its thing is so, and
every kind ends at its timeout with exit 3, never a wait that cannot end."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.load_sensitive  # each test spawns, polls and waits
WAIT = REPO_ROOT / "lib" / "proc_wait.py"


def _wait(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(WAIT), *args], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=hermetic_env(**env), timeout=patience(120),
                          check=False)  # fmt: skip


def test_pid_ends_when_the_process_is_gone_and_times_out_while_it_is_not() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                             stdin=subprocess.PIPE)  # fmt: skip
    try:
        r = _wait("pid", str(child.pid), "--timeout", "0.5")
        assert r.returncode == 3, r.stderr
        assert "still not so after" in r.stderr
    finally:
        child.communicate(input=b"", timeout=patience(30))
    r = _wait("pid", str(child.pid), "--timeout", "30")
    assert r.returncode == 0, r.stderr


def test_pid_ends_once_a_running_process_exits() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.5)"], stdin=subprocess.DEVNULL
    )
    try:
        r = _wait("pid", str(child.pid), "--timeout", "60")
        assert r.returncode == 0, r.stderr
    finally:
        child.wait(timeout=patience(30))


def test_pid_reads_a_stamp_whose_number_was_reused_as_gone(tmp_path: Path) -> None:
    pidfile = tmp_path / "x.pid"
    pidfile.write_text(f"{subprocess.os.getpid()} Mon Jan  1 00:00:00 2001\n", encoding="utf-8")
    r = _wait("pid", str(pidfile), "--timeout", "5")
    assert r.returncode == 0, r.stderr  # the stamped process is not this one: it is gone


def test_file_ends_when_the_file_is_there(tmp_path: Path) -> None:
    path = tmp_path / "done"
    r = _wait("file", str(path), "--timeout", "0.3")
    assert r.returncode == 3
    touch = f"import time, pathlib; time.sleep(0.4); pathlib.Path({str(path)!r}).touch()"
    writer = subprocess.Popen([sys.executable, "-c", touch], stdin=subprocess.DEVNULL)
    try:
        assert _wait("file", str(path), "--timeout", "60").returncode == 0
    finally:
        writer.wait(timeout=patience(30))


@pytest.mark.parametrize(
    ("doc", "spec", "met"),
    [
        ({"status": "done", "rc": 0}, "status=done,rc=0", True),
        ({"status": "done", "rc": 1}, "status=done,rc=0", False),
        ({"a": {"b": ["x", "y"]}}, "a.b.1=y", True),
        ({"ok": True, "why": None}, "ok=true,why=null", True),
        ({"rc": "0"}, "rc=0", True),  # a string compares as written
        ({"status": "running"}, "status=done", False),
        ({}, "missing=x", False),
    ],
)
def test_json_ends_when_every_key_holds_its_value(
    doc: dict, spec: str, met: bool, tmp_path: Path
) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert _wait("json", str(path), spec, "--timeout", "0.3").returncode == (0 if met else 3)


def test_json_reads_a_torn_or_missing_file_as_not_yet(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    assert _wait("json", str(path), "a=1", "--timeout", "0.3").returncode == 3
    path.write_text('{"a": 1', encoding="utf-8")
    assert _wait("json", str(path), "a=1", "--timeout", "0.3").returncode == 3


def _ship(tmp_path: Path, status: str, pid: int | None = None) -> Path:
    rec = tmp_path / "ship.json"
    rec.write_text(json.dumps({"status": status, "steps": [], "pid": pid, "result": str(rec)}),
                   encoding="utf-8")  # fmt: skip
    return rec


@pytest.mark.parametrize(("status", "rc"), [("ok", 0), ("failed", 1)])
def test_ship_ends_with_the_records_verdict(status: str, rc: int, tmp_path: Path) -> None:
    assert _wait("ship", str(_ship(tmp_path, status)), "--timeout", "5").returncode == rc


def test_ship_running_with_a_live_worker_times_out_and_a_gone_one_is_4(tmp_path: Path) -> None:
    rec = _ship(tmp_path, "running", subprocess.os.getpid())
    assert _wait("ship", str(rec), "--timeout", "0.5").returncode == 3
    dead = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
    dead.wait(timeout=patience(30))
    rec = _ship(tmp_path, "running", dead.pid)
    assert _wait("ship", str(rec), "--timeout", "5").returncode == 4


def test_land_passes_on_goh_land_waits_exit(tmp_path: Path) -> None:
    r = _wait("land", "GOH.NOSUCHITEM", "--timeout", "1", GOH_LANDQ_DIR=str(tmp_path))
    assert r.returncode == 2, r.stderr  # landq_look.wait: no request filed, none enqueuing
    assert "GOH.NOSUCHITEM" in r.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["pid", "notapid"],
        ["json", "x.json"],
        ["json", "x.json", "novalue"],
        ["file", "a", "b"],
        ["ship", "/nonexistent/ship.json"],
        ["land", "A", "B"],
    ],
)
def test_a_target_it_cannot_read_is_usage(args: list[str]) -> None:
    r = _wait(*args, "--timeout", "1")
    assert r.returncode == 2, (args, r.stderr)
