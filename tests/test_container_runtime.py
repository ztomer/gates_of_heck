"""Tests for lib/container_runtime.sh.

Ensures container runtime (colima / docker) lifecycle is managed with reference-counted
leases across concurrent processes, starting colima when docker is down and stopping
it on last release only if started by goh.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from conftest import REPO_ROOT

LIB = REPO_ROOT / "lib" / "container_runtime.sh"


def _setup_fakes(tmp_path: Path, *, docker_up: bool = False) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    status_file = tmp_path / "docker_status"
    status_file.write_text("up" if docker_up else "down")
    calls_log = tmp_path / "calls.log"
    calls_log.write_text("")
    fail_file = tmp_path / "colima_fail"

    docker_script = bin_dir / "docker"
    docker_script.write_text(f"""#!/usr/bin/env bash
status_file="{status_file}"
log_file="{calls_log}"
echo "docker $*" >> "$log_file"
if [ "$1" = "info" ]; then
    if [ -f "$status_file" ] && [ "$(cat "$status_file")" = "up" ]; then
        exit 0
    fi
    exit 1
fi
exit 0
""")
    docker_script.chmod(0o755)

    colima_script = bin_dir / "colima"
    colima_script.write_text(f"""#!/usr/bin/env bash
status_file="{status_file}"
log_file="{calls_log}"
fail_file="{fail_file}"
echo "colima $*" >> "$log_file"
if [ "$1" = "start" ]; then
    if [ -f "$fail_file" ]; then
        echo "fake colima: start failed" >&2
        exit 1
    fi
    echo "up" > "$status_file"
    exit 0
elif [ "$1" = "stop" ]; then
    echo "down" > "$status_file"
    exit 0
fi
exit 0
""")
    colima_script.chmod(0o755)


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["GOH_CONTAINER_STATE"] = str(state_dir)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["NO_COLOR"] = "1"
    env.update(extra)
    return env


def _run(
    tmp_path: Path, script: str, timeout: float = 20, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"\n{script}'],
        env=_env(tmp_path, **extra),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _spawn(tmp_path: Path, script: str, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["bash", "-c", f'. "{LIB}"\n{script}'],
        env=_env(tmp_path, **extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    )


def _wait_for(path: Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {path}")


def test_docker_down_starts_and_stops_colima(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=False)
    state = tmp_path / "state"
    script = """
goh_container_runtime_up "gate1" || exit 1
test -f "$GOH_CONTAINER_STATE/started_by_goh" || exit 2
test -f "$GOH_CONTAINER_STATE/leases/$$" || exit 3
goh_container_runtime_down || exit 4
"""
    res = _run(tmp_path, script)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"
    log = (tmp_path / "calls.log").read_text()
    assert "colima start" in log
    assert "colima stop" in log
    assert log.count("colima start") == 1
    assert log.count("colima stop") == 1
    assert not (state / "started_by_goh").exists()
    assert not (state / "leases").exists() or not list((state / "leases").iterdir())


def test_docker_already_up_never_starts_or_stops_colima(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=True)
    state = tmp_path / "state"
    script = """
goh_container_runtime_up "gate2" || exit 1
test ! -f "$GOH_CONTAINER_STATE/started_by_goh" || exit 2
test -f "$GOH_CONTAINER_STATE/leases/$$" || exit 3
goh_container_runtime_down || exit 4
"""
    res = _run(tmp_path, script)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"
    log = (tmp_path / "calls.log").read_text()
    assert "colima start" not in log
    assert "colima stop" not in log
    assert not (state / "started_by_goh").exists()
    assert not (state / "leases").exists() or not list((state / "leases").iterdir())


def test_two_leases_stop_only_on_last_down(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=False)
    ready_a = tmp_path / "ready_a"
    release_a = tmp_path / "release_a"
    done_a = tmp_path / "done_a"
    ready_b = tmp_path / "ready_b"
    release_b = tmp_path / "release_b"
    done_b = tmp_path / "done_b"

    script_a = f"""
goh_container_runtime_up "proc_a" || exit 1
: > "{ready_a}"
while [ ! -f "{release_a}" ]; do sleep 0.05; done
goh_container_runtime_down || exit 2
: > "{done_a}"
"""
    script_b = f"""
goh_container_runtime_up "proc_b" || exit 1
: > "{ready_b}"
while [ ! -f "{release_b}" ]; do sleep 0.05; done
goh_container_runtime_down || exit 2
: > "{done_b}"
"""

    proc_a = _spawn(tmp_path, script_a)
    try:
        _wait_for(ready_a)
        log = (tmp_path / "calls.log").read_text()
        assert "colima start" in log
        assert "colima stop" not in log

        proc_b = _spawn(tmp_path, script_b)
        try:
            _wait_for(ready_b)
            # Proc A releases its lease
            release_a.write_text("go")
            _wait_for(done_a)

            # Proc B is still alive; colima stop must NOT have been called
            log = (tmp_path / "calls.log").read_text()
            assert "colima stop" not in log

            # Proc B releases its lease
            release_b.write_text("go")
            _wait_for(done_b)

            # Now colima stop must have been called
            log = (tmp_path / "calls.log").read_text()
            assert "colima stop" in log
        finally:
            proc_b.terminate()
            proc_b.wait(timeout=5)
    finally:
        proc_a.terminate()
        proc_a.wait(timeout=5)

    assert proc_a.returncode == 0
    assert proc_b.returncode == 0


def test_stale_lease_does_not_hold_colima_up(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=False)
    state = tmp_path / "state"
    leases = state / "leases"
    leases.mkdir(parents=True, exist_ok=True)
    # Plant a marker and a stale lease with a dead PID (e.g. 999999)
    (state / "started_by_goh").write_text("")
    (leases / "999999").write_text("stale_proc")

    # Set docker up since colima was theoretically started
    (tmp_path / "docker_status").write_text("up")

    script = """
goh_container_runtime_up "live_proc" || exit 1
test -f "$GOH_CONTAINER_STATE/leases/$$" || exit 2
goh_container_runtime_down || exit 3
"""
    res = _run(tmp_path, script)
    assert res.returncode == 0, f"stdout: {res.stdout}, stderr: {res.stderr}"

    log = (tmp_path / "calls.log").read_text()
    assert "colima stop" in log
    assert not (state / "started_by_goh").exists()
    assert not (leases / "999999").exists()


def test_colima_start_fails_returns_nonzero_with_log(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=False)
    state = tmp_path / "state"
    (tmp_path / "colima_fail").write_text("fail")

    script = 'goh_container_runtime_up "failing_job"'
    res = _run(tmp_path, script)
    assert res.returncode != 0
    assert "colima start failed" in res.stderr
    assert "colima_start.log" in res.stderr
    assert "✗" in res.stderr
    assert not (state / "started_by_goh").exists()
    assert not (state / "leases").exists() or not list((state / "leases").iterdir())


def test_no_colima_binary_returns_nonzero_never_skips(tmp_path: Path) -> None:
    _setup_fakes(tmp_path, docker_up=False)
    state = tmp_path / "state"
    script = 'goh_container_runtime_up "no_colima_job"'
    res = _run(tmp_path, script, COLIMA="/nonexistent/colima")
    assert res.returncode != 0
    assert "no colima to start" in res.stderr
    assert "✗" in res.stderr
    assert "skip" not in res.stderr.lower()
    assert "skip" not in res.stdout.lower()
    assert not (state / "started_by_goh").exists()
    assert not (state / "leases").exists() or not list((state / "leases").iterdir())
