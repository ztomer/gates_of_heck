"""lib/bench_lock.sh: a measurement holds off every goh gate on the host, and nothing deadlocks.

Every wall-clock number in docs/BACKLOG.md needs a quiet box, and two days of them were taken
at load 7-31 beside other sessions' gates (2026-10-08). Each test runs against a private lock
dir (GOH_BENCH_LOCK_DIR) and real processes: a holder that is `kill -9`ed must release. The lock's
2 s re-check is 0.1 s here (FAST_POLL), and a test waits for what a process SAYS (GOH.PYSLOWTESTS).
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from bench_load_fakes import FAST_POLL, busy_for, controls, fake_load, gate_load, says
from conftest import REPO_ROOT
from host_locks import scrubbed  # the one env scrub: it keeps the private roots
from orphan_kit import wait_for_file
from timing_bounds import GiveUp, assert_sooner

LIB = REPO_ROOT / "lib" / "bench_lock.sh"
WATCH_S = 2.0
pytestmark = pytest.mark.load_sensitive(reason="host locks held by spawned holders")


def _env(lock: Path, **extra: str) -> dict[str, str]:
    """A private bench lock, the desktop and GPU locks beside it, never another session's."""
    env = scrubbed(os.environ, ("GOH_BENCH_", "ZTOOLS_GPU_LOCK_OWNER"))
    private = {
        "GOH_BENCH_LOCK_DIR": str(lock),
        "GOH_BENCH_DESKTOP_LOCK_DIR": str(lock.parent / "desktop.lock"),
        "ZTOOLS_GPU_LOCK_DIR": str(lock.parent / "gpu.lock"),
    }
    return {**env, **private, **FAST_POLL, **extra}


def _bash(lock: Path, script: str, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(["bash", "-c", f'. "{LIB}"\n{script}'], env=_env(lock, **extra),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)  # fmt: skip


def _run(lock: Path, script: str, timeout: float = 20, **extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'. "{LIB}"\n{script}'], env=_env(lock, **extra),
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _wait_for(path: Path, seconds: float = 10) -> None:
    give_up = GiveUp(seconds)
    while not path.exists():
        assert not give_up.passed(), f"{path} never appeared"
        time.sleep(0.05)


def _hold(lock: Path, tmp_path: Path) -> subprocess.Popen[str]:
    """A measurement that holds the host until killed; `ready` once it has drained."""
    p = _bash(lock, f'bench_lock_exclusive "test hold" && : > "{tmp_path}/ready" && exec sleep 60')
    _wait_for(tmp_path / "ready")
    return p


def test_a_gate_joins_with_no_process_spawned(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    (lock / "gates").mkdir(parents=True)
    r = _run(
        lock,
        f'cd "{tmp_path}"; PATH=/nonexistent; bench_lock_join "py gate"; echo "$BENCH_LOCK_ENTRY"',
    )
    assert r.returncode == 0, r.stderr
    entry = Path(r.stdout.strip())
    # The stage, then the repo it gates: what tools/load.py names when it refuses (GOH.LOADX).
    assert entry.parent == lock / "gates" and entry.read_text() == f"py gate\n{tmp_path}\n"


def test_a_gate_waits_while_a_measurement_holds_and_runs_when_it_ends(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder, said = _hold(lock, tmp_path), tmp_path / "said"
    gate = _bash(lock, f'exec 2>"{said}"; bench_lock_join "py gate"; : > "{tmp_path}/ran"')
    says(said, "waiting for a measurement", gate)
    time.sleep(0.5)  # five re-checks
    assert not (tmp_path / "ran").exists(), "a gate ran while a measurement held the host"
    holder.terminate()  # a clean end: bash's EXIT trap would release; here the pid dies
    holder.wait(timeout=10)
    gate.wait(timeout=15)
    assert (tmp_path / "ran").exists()


def test_a_waiting_gate_is_never_counted_as_a_running_one(tmp_path: Path) -> None:
    """A waiter that re-registered on every re-check stayed in gates/ while it ran its stale and
    nested checks (a `ps` each), so the drain saw waiters as running gates: dozens of them on a
    loaded box and it never drained (2026-10-08, every push gate). A slow `ps` makes it certain."""
    lock, shim = tmp_path / "lock", tmp_path / "bin"
    shim.mkdir()
    (shim / "ps").write_text('#!/bin/bash\n/bin/sleep 0.2\nexec /bin/ps "$@"\n')
    (shim / "ps").chmod(0o755)
    holder = _hold(lock, tmp_path)
    path = f"{shim}:{os.environ['PATH']}"
    waiters = [_bash(lock, 'bench_lock_join "py gate"', PATH=path) for _ in range(5)]
    try:
        give_up = GiveUp(20)
        while len(list((lock / "waiting").glob("*")) if (lock / "waiting").is_dir() else []) < 5:
            assert not give_up.passed(), "the gates never queued"
            time.sleep(0.05)
        seen, end = set(), time.monotonic() + WATCH_S  # a window watched, not a deadline
        while time.monotonic() < end:
            seen |= {p.name for p in (lock / "gates").iterdir()}
            time.sleep(0.05)
        assert len(seen) == 0, f"waiting gates were registered as running: {sorted(seen)}"
    finally:
        for w in waiters:
            w.kill()
            w.wait()
        holder.kill()
        holder.wait()


@pytest.mark.parametrize(("lib", "reader"), [
    ("lib/bench_lock.sh", "BENCH_LOCK_DIR=/nonexistent; _bench_owner_field 1"),
    ("lib/desktop_lock/desktop_lock.sh", "DESKTOP_LOCK_DIR=/nonexistent; _desktop_lock_field 1"),
])  # fmt: skip
def test_an_owner_read_as_the_claim_vanishes_is_silent(lib: str, reader: str) -> None:
    """`done <file 2>/dev/null` opens the file BEFORE it silences the error: a gate that read an
    owner the instant its holder released printed `No such file or directory` (2026-10-08)."""
    r = subprocess.run(["bash", "-c", f'. "{REPO_ROOT / lib}"; {reader}; echo "rc=$?"'],
                       capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert (r.stdout, r.stderr) == ("rc=1\n", "")


def test_a_killed_measurement_releases_the_host(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    holder = _hold(lock, tmp_path)
    holder.kill()
    holder.wait(timeout=10)
    r = _run(lock, 'bench_lock_join "py gate" && echo joined')
    assert r.stdout.strip() == "joined", r.stderr


def test_a_measurement_waits_for_a_running_gate_and_drops_a_dead_ones_entry(tmp_path, request):
    """GOH.BENCHLOCKFLAKE: the gate holds until the test SEES the measurement wait, never 3 s."""
    lock, said, release = tmp_path / "lock", tmp_path / "said", tmp_path / "release"
    (lock / "gates").mkdir(parents=True)
    (lock / "gates" / "999999").write_text("long gone\n")  # a gate SIGKILLed mid-run
    hold = wait_for_file(release, seconds=60)
    gate = _bash(lock, f'bench_lock_join "rust gate"; : > "{tmp_path}/joined"; {hold}')
    request.addfinalizer(release.touch)  # a red run ends both processes too
    _wait_for(tmp_path / "joined")
    m = _bash(lock, f'bench_lock_exclusive "test" 2>"{said}" && echo held')
    says(said, "rust gate", m)  # ended, it held the host while a gate was running
    release.touch()
    assert m.communicate(timeout=60)[0].strip() == "held", said.read_text()
    assert not (lock / "gates" / "999999").exists()
    gate.wait(timeout=10)


def test_a_gate_nested_in_a_joined_gate_never_waits(tmp_path: Path) -> None:
    """GOH_* stripped, as this repo's suite does inside its own push: the ancestor walk decides."""
    lock = tmp_path / "lock"
    go, said = tmp_path / "go", tmp_path / "said"
    outer = _bash(lock, f'bench_lock_join outer; : > "{tmp_path}/outer"; {wait_for_file(go)}; '
                        f'env -u GOH_BENCH_JOINED bash -c \'. "{LIB}"; bench_lock_join inner\' '
                        f'&& : > "{tmp_path}/inner"')  # fmt: skip
    _wait_for(tmp_path / "outer")
    claim = _bash(
        lock, f'exec 2>"{said}"; bench_lock_exclusive "test" && : > "{tmp_path}/held"',
        GOH_BENCH_WAIT="60",
    )  # fmt: skip
    says(said, "waiting for running gates", claim)  # the claim is made; it waits for `outer`
    go.touch()
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
    holder = _bash(lock, f'bench_lock_exclusive m && : > "{tmp_path}/ready" && exec sleep 60',
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


QUIET = REPO_ROOT / "tools" / "quiet.sh"


def _quiet_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A private lock and desktop lock, fast polls, and a steady control (the real one times 200
    spawns, which the live box's load would move)."""
    base = {"GOH_BENCH_DESKTOP_LOCK_DIR": str(tmp_path / "desktop.lock"), "GOH_BENCH_POLL": "0.1",
            "GOH_BENCH_CONTROL": "echo 1.0", "ZTOOLS_GPU_LOCK_DIR": str(tmp_path / "gpu.lock")}  # fmt: skip
    return _env(tmp_path / "lock", **{**base, **extra})


def _quiet(tmp_path: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    env = _quiet_env(tmp_path, **extra)
    return subprocess.run(["bash", str(QUIET), *args], env=env, capture_output=True, text=True,
                          timeout=60, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def _reads(tmp_path: Path) -> list[str]:
    return (tmp_path / "reads").read_text().splitlines()


def test_quiet_runs_the_measurement_holding_the_host_the_desktop_and_the_gpu(
    tmp_path: Path,
) -> None:
    lock, desk, gpu = tmp_path / "lock", tmp_path / "desktop.lock", tmp_path / "gpu.lock"
    probe = (
        f'test -f "{lock}/exclusive/owner" && test -d "{desk}" && test -f "{gpu}/owner" && exit 3'
    )
    r = _quiet(tmp_path, "--max-load", "1000", "--", "bash", "-c", probe)
    assert r.returncode == 3, r.stdout + r.stderr  # the command's own status, and it saw all three
    assert "load" in r.stdout and not (lock / "exclusive").exists() and not desk.exists()
    assert not gpu.exists()


def _gate_stream(lock: Path, running: Path) -> subprocess.Popen[str]:
    """Other sessions: a new 0.6 s gate every 0.2 s, so some gate is always running."""
    gate = (f'. "{LIB}"; bench_lock_join stream; : > "{running}/$$"; sleep 0.6; '
            f'rm -f "{running}/$$" "$BENCH_LOCK_ENTRY"')  # fmt: skip
    loop = f"while kill -0 {os.getpid()} 2>/dev/null; do bash -c {gate!r} & sleep 0.2; done"
    return subprocess.Popen(["bash", "-c", loop], env=_env(lock), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, stdin=subprocess.DEVNULL)  # fmt: skip


def test_quiet_takes_its_place_before_it_judges_the_box(tmp_path: Path) -> None:
    """BACKLOG 3.1: queued at 09:57, it waited 2 h for load < 4 holding nothing, and every new gate
    from the other sessions started ahead of it -- a reader-preferring queue starves its writer.
    It claims the host FIRST: new gates queue behind it, the running ones drain, the load falls."""
    (tmp_path / "lock" / "gates").mkdir(parents=True)
    (tmp_path / "running").mkdir()
    stream = _gate_stream(tmp_path / "lock", tmp_path / "running")
    try:
        r = _quiet(tmp_path, "--deadline", "20", "--settle", "10", "--", "true",
                   **gate_load(tmp_path))  # fmt: skip
    finally:
        os.killpg(stream.pid, 9)
        stream.wait()
    assert r.returncode == 0, r.stdout + r.stderr
    reads = _reads(tmp_path)
    assert reads and all(x.startswith("held") for x in reads), reads  # judged only while holding
    assert reads[-1] == "held 1", reads


def test_quiet_lets_go_between_attempts_while_the_box_stays_busy(tmp_path: Path) -> None:
    """With every gate drained and the box still busy (an xctest, Spotlight), holding on would
    block every session's commits for nothing: it lets go, waits, and claims again."""
    owners, script = tmp_path / "owners", tmp_path / "load.sh"
    script.write_text(  # busy until a SECOND claim reads it; one held through never ends
        f'#!/bin/bash\nsed -n 3p "{tmp_path}/lock/exclusive/owner" >> "{owners}"\n'
        f'[ "$(sort -u "{owners}" | wc -l)" -ge 2 ] && echo 1 || echo 50\n'
    )
    script.chmod(0o755)
    r = _quiet(tmp_path, "--settle", "1", "--retry", "0.2", "--deadline", "40", "--", "true",
               GOH_BENCH_LOADAVG=str(script))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert "letting go" in r.stderr, r.stderr


def test_quiet_refuses_after_its_deadline_and_names_what_is_busy(tmp_path: Path) -> None:
    r = _quiet(tmp_path, "--max-load", "0", "--deadline", "1", "--settle", "0", "--retry", "0.1",
               "--", "true")  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not quiet" in r.stderr and "%CPU" in r.stderr, r.stderr
    assert not (tmp_path / "lock" / "exclusive").exists()


def test_a_hold_longer_than_the_cap_is_refused_up_front(tmp_path: Path) -> None:
    """BACKLOG 3.2: a hold blocks every session's commits, so it is short and says how long. One
    that needs longer than the cap is split, never extended."""
    r = _quiet(tmp_path, "--max-load", "1000", "--hold", "1000", "--", "true",
               GOH_BENCH_MAX_HOLD="900")  # fmt: skip
    assert r.returncode != 0 and "split" in r.stderr, r.stdout + r.stderr
    assert not (tmp_path / "lock" / "exclusive").exists()


def test_a_waiting_gate_names_the_hold_and_when_it_ends(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    env = _quiet_env(tmp_path)  # the steady control: the real one times 200 spawns under load
    hold = subprocess.Popen(
        ["bash", str(QUIET), "--max-load", "1000", "--hold", "30", "--label", "bench 4.1", "--",
         "bash", "-c", f': > "{tmp_path}/running"; exec sleep 3'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        _wait_for(tmp_path / "running")
        r = _run(lock, "bench_lock_join gate && echo joined", timeout=30)
    finally:
        hold.kill()
        hold.wait()
    assert r.stdout.strip() == "joined", r.stderr
    assert "bench 4.1" in r.stderr and "at the latest" in r.stderr, r.stderr


def test_a_run_that_outlives_its_hold_is_named_and_fails(tmp_path: Path) -> None:
    """Past its hold the gates resume, so what it measured after that was not taken quiet."""
    r = _quiet(tmp_path, "--max-load", "1000", "--hold", "1", "--", "sleep", "2.5")
    assert r.returncode != 0 and "outlived its" in r.stderr, r.stdout + r.stderr


def test_the_default_hold_is_fifteen_minutes() -> None:
    env = scrubbed(os.environ, ("GOH_BENCH_",))
    r = subprocess.run(["bash", "-c", f'. "{LIB}"; echo "$BENCH_LOCK_MAX_HOLD"'], env=env,
                       capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.stdout.strip() == "900", r.stdout + r.stderr


def test_a_gate_behind_an_overrunning_hold_proceeds_at_the_hold_not_the_cap(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    env = _quiet_env(tmp_path)  # the steady control: the real one times 200 spawns under load
    hold = subprocess.Popen(
        ["bash", str(QUIET), "--max-load", "1000", "--hold", "2", "--",
         "bash", "-c", f': > "{tmp_path}/running"; exec sleep 30'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        _wait_for(tmp_path / "running")
        start = time.monotonic()
        r = _run(lock, "bench_lock_join gate && echo joined", timeout=20)
        waited = time.monotonic() - start
    finally:
        hold.kill()
        hold.wait()
    assert r.stdout.strip() == "joined", r.stderr
    assert_sooner(waited, 2.5, 30, "a gate behind a 2 s hold, not its 30 s run")


def test_a_run_whose_controls_move_is_noisy_not_a_number(tmp_path: Path) -> None:
    """BACKLOG 3.3: background goh cannot hold (an xctest, Spotlight) moves inside a hold, and only
    a control taken before AND after the run sees it."""
    r = _quiet(tmp_path, "--max-load", "1000", "--", "true", **controls(tmp_path, "0.40", "0.80"))
    assert r.returncode != 0 and "noisy" in r.stderr, r.stdout + r.stderr
    steady = _quiet(tmp_path, "--max-load", "1000", "--", "true",
                    **controls(tmp_path, "0.40", "0.42"))  # fmt: skip
    assert steady.returncode == 0, steady.stdout + steady.stderr
    assert "control 0.40 s before, 0.42 s after" in steady.stdout, steady.stdout


def test_the_floor_is_measured_holding_the_host(tmp_path: Path) -> None:
    """The threshold comes from the box: `--floor` drains every gate and samples what is left."""
    load = fake_load(tmp_path, [9, 7, 8])
    r = _quiet(tmp_path, "--floor", "--settle", "3", **load, **controls(tmp_path, "0.41"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "floor: load 7 min" in r.stdout and "control 0.41 s" in r.stdout, r.stdout
    assert all(x.startswith("held") for x in _reads(tmp_path)), _reads(tmp_path)
    assert not (tmp_path / "lock" / "exclusive").exists()


def test_a_new_measurement_lets_the_gates_already_waiting_in_first(tmp_path: Path) -> None:
    """Back to back, a series of measurements was one hold to everyone else: each claim followed
    the last release at once, so no gate queued behind run N started before run N+1 (servers,
    2026-10-08). The gates already waiting when the host comes free go first (phase-fair)."""
    lock = tmp_path / "lock"
    said, go = tmp_path / "said", tmp_path / "go"
    series = _bash(lock, f'exec 2>"{tmp_path}/series"; bench_lock_exclusive run1 && '
                   f': > "{tmp_path}/ready" && {wait_for_file(go)}; bench_lock_release; '
                   f'bench_lock_exclusive run2 && : > "{tmp_path}/second"', GOH_BENCH_WAIT="30")  # fmt: skip
    _wait_for(tmp_path / "ready")
    # A re-check of 0.1 s could win the race by itself: at 1 s only the yield lets this gate in.
    gate = _bash(lock, f'sleep() {{ command sleep 1; }}; exec 2>"{said}"; bench_lock_join gate; '
                       f': > "{tmp_path}/ran"; {wait_for_file(tmp_path / "end")}')  # fmt: skip
    says(said, "waiting for a measurement", gate)  # behind run 1
    go.touch()  # run 1 ends, and run 2 claims at once
    says(tmp_path / "series", "waiting for running gates", series)  # run 2 drains the one it let in
    (tmp_path / "end").touch()
    series.wait(timeout=30)
    gate.wait(timeout=30)
    assert (tmp_path / "ran").exists() and (tmp_path / "second").exists()
    assert (tmp_path / "ran").stat().st_mtime_ns <= (tmp_path / "second").stat().st_mtime_ns


def test_the_cap_bounds_the_whole_block_not_only_the_run(tmp_path: Path) -> None:
    """The drain and the settle block every gate too: the first cap bounded only the run, so a
    claim could block for its settle AND its hold. A run that would end past the cap, counted from
    the claim, lets go instead."""
    load = busy_for(tmp_path, 60)  # the old claim held through it, to the cap
    lock = tmp_path / "lock"
    env = _quiet_env(tmp_path, GOH_BENCH_MAX_HOLD="40", **load)  # a budget of 2 s: 40 - 38
    hold = subprocess.Popen(
        ["bash", str(QUIET), "--hold", "38", "--settle", "20", "--retry", "0.2", "--",
         "sleep", "2.5"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        _wait_for(lock / "exclusive" / "owner")
        start = time.monotonic()
        r = _run(lock, "bench_lock_join gate && echo joined", timeout=50)
        waited = time.monotonic() - start
    finally:
        hold.kill()  # the box stays busy for a minute: the let-go is the claim, not the run
        hold.wait()
    # The budget (2 s) + a waiter's 2 s re-check. The old claim (f69c1fe^) held past the 40 s cap:
    # the join timed out at 50 s.
    assert r.stdout.strip() == "joined", r.stderr
    assert_sooner(waited, 4, 40, "a claim past its budget letting go, not the cap")
    err = hold.stderr.read()
    assert "letting go" in err, err


def test_a_drain_past_the_budget_lets_go_at_the_budget(tmp_path: Path) -> None:
    """BACKLOG 4.3 a private app's j4 (2026-10-08): the claim waited 579 s for two pre-push gates,
    every new gate blocked behind it, then let go unmeasured -- its run no longer fit the cap -- and
    blamed the load ("not goh's") at 5.58, under the max. A drain is bounded by the budget."""
    lock = tmp_path / "lock"
    slow = _bash(lock, f'bench_lock_join pre-push; : > "{tmp_path}/in"; sleep 60')
    _wait_for(tmp_path / "in")
    q = subprocess.Popen(
        ["bash", str(QUIET), "--max-load", "1000", "--hold", "37", "--retry", "0.5", "--", "true"],
        env=_quiet_env(tmp_path, GOH_BENCH_MAX_HOLD="40"), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        _wait_for(lock / "exclusive" / "owner")
        start = time.monotonic()
        r = _run(lock, "bench_lock_join gate && echo joined", timeout=50)
        waited = time.monotonic() - start
        slow.kill()  # the gate it drained ends: the retry claims and runs
        slow.wait()
        q.wait(timeout=40)
    finally:
        q.kill()
        slow.kill()
        slow.wait()
    # The budget (3 s: 40 - 37) + a waiter's 2 s re-check: 5.2 s at load 100 (2026-10-08). The old
    # drain held until a waiter voided it at the 40 s cap.
    assert r.stdout.strip() == "joined", r.stderr
    assert_sooner(waited, 5, 40, "a drain past its budget letting go, not the cap")
    err = q.stderr.read()
    assert q.returncode == 0 and "budget" in err and "not goh's" not in err, err
