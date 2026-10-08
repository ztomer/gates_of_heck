"""lib/bench_lock.sh: a measurement holds off every goh gate on the host, and nothing deadlocks.

Every wall-clock number in docs/BACKLOG.md needs a quiet box, and two days of them were taken
at load 7-31 beside other sessions' gates (2026-10-08). Each test runs against a private lock
dir (GOH_BENCH_LOCK_DIR) and real processes: a holder that is `kill -9`ed must release.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from conftest import REPO_ROOT

LIB = REPO_ROOT / "lib" / "bench_lock.sh"


def _env(lock: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_BENCH_")}
    return {**env, "GOH_BENCH_LOCK_DIR": str(lock), **extra}


def _bash(lock: Path, script: str, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(["bash", "-c", f'. "{LIB}"\n{script}'], env=_env(lock, **extra),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # fmt: skip


def _run(lock: Path, script: str, timeout: float = 20, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB}"\n{script}'], env=_env(lock, **extra),
                          capture_output=True, text=True, timeout=timeout)  # fmt: skip


def _wait_for(path: Path, seconds: float = 10) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path} never appeared"
        time.sleep(0.05)


def _hold(lock: Path, tmp_path: Path) -> subprocess.Popen[str]:
    """A measurement that holds the host until killed; `ready` once it has drained."""
    p = _bash(lock, f'bench_lock_exclusive "test hold" && : > "{tmp_path}/ready" && sleep 60')
    _wait_for(tmp_path / "ready")
    return p


def test_a_gate_joins_with_no_process_spawned(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    (lock / "gates").mkdir(parents=True)
    r = _run(lock, 'PATH=/nonexistent; bench_lock_join "py gate"; echo "$BENCH_LOCK_ENTRY"')
    assert r.returncode == 0, r.stderr
    entry = Path(r.stdout.strip())
    assert entry.parent == lock / "gates" and entry.read_text() == "py gate\n"


def test_a_gate_waits_while_a_measurement_holds_and_runs_when_it_ends(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    gate = _bash(lock, f'bench_lock_join "py gate"; : > "{tmp_path}/ran"')
    time.sleep(2.5)
    assert not (tmp_path / "ran").exists(), "a gate ran while a measurement held the host"
    holder.terminate()  # a clean end: bash's EXIT trap would release; here the pid dies
    holder.wait(timeout=10)
    gate.wait(timeout=15)
    assert (tmp_path / "ran").exists()
    assert "waiting for a measurement" in gate.stderr.read()


def test_a_killed_measurement_releases_the_host(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    holder.kill()
    holder.wait(timeout=10)
    r = _run(lock, 'bench_lock_join "py gate" && echo joined')
    assert r.stdout.strip() == "joined", r.stderr


def test_a_measurement_waits_for_a_running_gate_and_drops_a_dead_ones_entry(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    (lock / "gates").mkdir(parents=True)
    (lock / "gates" / "999999").write_text("long gone\n")  # a gate SIGKILLed mid-run
    gate = _bash(lock, f'bench_lock_join "rust gate"; : > "{tmp_path}/joined"; sleep 3')
    _wait_for(tmp_path / "joined")
    t0 = time.monotonic()
    r = _run(lock, 'bench_lock_exclusive "test" && echo held', timeout=30)
    assert r.stdout.strip() == "held", r.stderr
    assert time.monotonic() - t0 >= 1.5, "the measurement started while a gate was running"
    assert "rust gate" in r.stderr
    assert not (lock / "gates" / "999999").exists()
    gate.wait(timeout=10)


def test_a_gate_nested_in_a_joined_gate_never_waits(tmp_path: Path) -> None:
    """GOH_* stripped, as this repo's suite does inside its own push: the ancestor walk decides."""
    lock = tmp_path / "lock"
    outer = _bash(lock, f'bench_lock_join outer; : > "{tmp_path}/outer"; sleep 4; '
                        f'env -u GOH_BENCH_JOINED bash -c \'. "{LIB}"; bench_lock_join inner\' '
                        f'&& : > "{tmp_path}/inner"')  # fmt: skip
    _wait_for(tmp_path / "outer")
    claim = _bash(
        lock, f'bench_lock_exclusive "test" && : > "{tmp_path}/held"', GOH_BENCH_WAIT="20"
    )
    time.sleep(1)  # the claim is made; it now waits for `outer`
    outer.wait(timeout=15)
    assert (tmp_path / "inner").exists(), (
        "a nested gate waited on a measurement waiting on its parent"
    )
    claim.wait(timeout=15)
    assert (tmp_path / "held").exists()


def test_the_measurements_own_gates_run(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    r = _run(lock, f'bench_lock_exclusive m && env -i PATH="$PATH" GOH_BENCH_LOCK_DIR="{lock}" '
                   f'bash -c \'. "{LIB}"; bench_lock_join child\' && echo ran')  # fmt: skip
    assert r.stdout.strip() == "ran", r.stderr


def test_an_overdue_holder_is_void(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder = _bash(lock, f'bench_lock_exclusive m && : > "{tmp_path}/ready" && sleep 60',
                   GOH_BENCH_MAX_HOLD="1")  # fmt: skip
    _wait_for(tmp_path / "ready")
    time.sleep(2.2)
    r = _run(lock, "bench_lock_join gate && echo joined")
    holder.kill()
    assert r.stdout.strip() == "joined", r.stderr


def test_a_second_measurement_waits_for_the_first(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    r = _run(lock, "bench_lock_exclusive second", GOH_BENCH_WAIT="2")
    holder.kill()
    assert r.returncode == 1 and "another measurement holds the host" in r.stderr, r.stderr


def _waits_then_runs(tmp_path: Path, argv: list[str], cwd: Path, stdin: str = "") -> str:
    """Run a real entry point while the host is held: it must wait, then finish once released."""
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    gate = subprocess.Popen(argv, cwd=cwd, env=_env(lock), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)  # fmt: skip
    time.sleep(2.5)
    still = gate.poll() is None
    holder.kill()
    holder.wait(timeout=10)
    _, err = gate.communicate(stdin, timeout=60)
    assert still, f"{argv[1]} ran while a measurement held the host:\n{err}"
    assert "waiting for a measurement" in err, err
    return err


def test_every_gate_joins_through_goh_init(repo: Path, tmp_path: Path) -> None:
    (repo / "a.py").write_text("x = 1\n")
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "py_staged.sh"), "."], repo)


def test_local_ci_joins(repo: Path, tmp_path: Path) -> None:
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "local_ci.sh"), "--step", "true"],
                     repo)  # fmt: skip


def test_the_push_gate_joins(repo: Path, tmp_path: Path) -> None:
    _waits_then_runs(tmp_path, ["bash", str(REPO_ROOT / "gates" / "push_gate.sh"), "origin", "u"],
                     repo)  # fmt: skip


QUIET = REPO_ROOT / "tools" / "quiet.sh"


def _quiet(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = _env(tmp_path / "lock", GOH_BENCH_DESKTOP_LOCK_DIR=str(tmp_path / "desktop.lock"))
    return subprocess.run(["bash", str(QUIET), *args], env=env, capture_output=True, text=True,
                          timeout=60)  # fmt: skip


def test_quiet_runs_the_measurement_holding_the_host_and_the_desktop(tmp_path: Path) -> None:
    lock, desk = tmp_path / "lock", tmp_path / "desktop.lock"
    probe = f'test -f "{lock}/exclusive/owner" && test -d "{desk}" && exit 3'
    r = _quiet(tmp_path, "--max-load", "1000", "--", "bash", "-c", probe)
    assert r.returncode == 3, r.stdout + r.stderr  # the command's own status, and it saw both
    assert "load" in r.stdout and not (lock / "exclusive").exists() and not desk.exists()


def test_quiet_refuses_a_box_that_will_not_settle_and_names_what_is_busy(tmp_path: Path) -> None:
    r = _quiet(tmp_path, "--max-load", "0", "--settle", "1", "--", "true")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not quiet" in r.stderr and "%CPU" in r.stderr, r.stderr
    assert not (tmp_path / "lock" / "exclusive").exists()
