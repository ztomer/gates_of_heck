"""The share is LIVE (GOH.SHAREGROW): a running gate grows when another leaves, shrinks when one is
admitted.

2026-11-17 21:58 the SELECTAUDIT land's coverage step ran the goh suite at `-n 4` for 11+ minutes
while the box sat at load 7.9 of 16 cores: its share was taken at admission beside three others and
never grew after they left. The count is driven here by held slot files and a fake step count, never
by the clock: each "batch" is every worker taking a token once, as before each test.
"""

from __future__ import annotations

import fcntl
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import worker_share


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    (tmp_path / "slots").mkdir(exist_ok=True)
    return {
        "GOH_GATE_CORES": "16",
        "GOH_GATE_SLOT_DIR": str(tmp_path / "slots"),
        "GOH_SHARE_OWN": "0",  # this gate holds a slot (slot-1, held below)
        **extra,
    }


def _hold(tmp_path: Path, name: str):
    (tmp_path / "slots").mkdir(exist_ok=True)
    f = open(tmp_path / "slots" / name, "w")  # noqa: SIM115  -- held until closed
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def _batch(pool: worker_share.Pool, workers: int) -> int:
    """Every worker takes a token once (as before a test); how many run. All are given back."""
    fds = [pool.take(w, wait=False) for w in range(workers)]
    got = [fd for fd in fds if fd is not None]
    for fd in got:
        os.close(fd)
    return len(got)


def _pool(tmp_path: Path, env: dict[str, str], ceiling: int = 16) -> worker_share.Pool:
    return worker_share.Pool(
        str(tmp_path / "pool"), ceiling, width=lambda: worker_share.live(env), period=0
    )


def test_a_gate_admitted_beside_another_grows_to_the_whole_share_once_it_leaves(tmp_path):
    env = _env(tmp_path)
    me, other = _hold(tmp_path, "slot-1"), _hold(tmp_path, "slot-0")
    try:
        pool = _pool(tmp_path, env)
        assert _batch(pool, 16) == 8  # S/2 beside the other gate
        other.close()
        assert _batch(pool, 16) == 16  # S within one batch of its leaving
    finally:
        me.close()


def test_a_gate_admitted_later_shrinks_a_running_one(tmp_path):
    env = _env(tmp_path)
    me = _hold(tmp_path, "slot-1")
    try:
        pool = _pool(tmp_path, env)
        assert _batch(pool, 16) == 16
        newcomer = _hold(tmp_path, "short-0")  # either lane runs a suite
        try:
            assert _batch(pool, 16) == 8
        finally:
            newcomer.close()
    finally:
        me.close()


def test_a_share_that_shrinks_mid_test_lets_no_new_test_start_past_it(tmp_path):
    """Tokens held when the share halves are not taken back; no NEW one is given above it."""
    env = _env(tmp_path)
    me = _hold(tmp_path, "slot-1")
    try:
        pool = _pool(tmp_path, env)
        running = [pool.take(w, wait=False) for w in range(16)]
        assert None not in running
        newcomer = _hold(tmp_path, "slot-0")
        try:
            for fd in running[:12]:  # twelve tests end; four (tokens 12-15) still run
                os.close(fd)
            assert _batch(pool, 16) == 8  # 8 below the new share; 12 above stay idle
        finally:
            newcomer.close()
            for fd in running[12:]:
                os.close(fd)
    finally:
        me.close()


def test_the_ceiling_bounds_the_tokens_and_a_run_outside_any_slot_counts_itself(tmp_path):
    env = _env(tmp_path, GOH_SHARE_OWN="1")
    assert _batch(_pool(tmp_path, env, ceiling=12), 16) == 12
    other = _hold(tmp_path, "slot-0")
    try:
        assert _batch(_pool(tmp_path, env, ceiling=12), 16) == 8
    finally:
        other.close()


def test_sibling_steps_divide_the_share_while_they_run_and_return_it_when_they_end(tmp_path):
    """gates/local_ci.sh writes how many of its steps run now; each enclosing scheduler divides."""
    outer, inner = tmp_path / "outer", tmp_path / "inner"
    outer.write_text("2\n")
    inner.write_text("2\n")
    env = _env(tmp_path, GOH_SHARE_STEPS=f"{outer}:{inner}")
    me = _hold(tmp_path, "slot-1")
    try:
        pool = _pool(tmp_path, env)
        assert _batch(pool, 16) == 4
        inner.write_text("1\n")
        assert _batch(pool, 16) == 8
        outer.unlink()  # its scheduler is gone: divides by nothing
        assert _batch(pool, 16) == 16
    finally:
        me.close()


def test_the_count_is_reread_once_a_period_not_per_test(tmp_path):
    env = _env(tmp_path)
    now = [0.0]
    calls = []

    def width() -> int:
        calls.append(1)
        return worker_share.live(env)

    me = _hold(tmp_path, "slot-1")
    try:
        pool = worker_share.Pool(
            str(tmp_path / "pool"), 16, width, period=2.0, clock=lambda: now[0]
        )
        _batch(pool, 16)
        _batch(pool, 16)
        assert len(calls) == 1
        now[0] = 2.0
        _batch(pool, 16)
        assert len(calls) == 2
    finally:
        me.close()


def _deps_binary(tmp_path: Path) -> Path:
    deps = tmp_path / "target" / "debug" / "deps"
    deps.mkdir(parents=True)
    prog = deps / "suite-0123"
    prog.write_text('#!/bin/sh\necho "$RUST_TEST_THREADS/$GOH_GATE_WORKERS"\n')
    prog.chmod(prog.stat().st_mode | stat.S_IXUSR)
    return prog


def _run(tmp_path: Path, prog: Path, **extra: str) -> str:
    env = {**hermetic_env(), **_env(tmp_path), "GOH_LIVE": "1", **extra}
    r = subprocess.run(
        [sys.executable, "-S", str(REPO_ROOT / "lib" / "worker_share.py"), "run", str(prog)],
        env=env,
        capture_output=True,
        text=True,
        timeout=patience(60),
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_cargo_test_hands_each_test_binary_the_share_live(tmp_path):
    prog = _deps_binary(tmp_path)
    me, other = _hold(tmp_path, "slot-1"), _hold(tmp_path, "slot-0")
    try:
        ours = {"RUST_TEST_THREADS": "8", "GOH_GATE_WORKERS": "8"}
        assert _run(tmp_path, prog, **ours) == "8/8"
        other.close()
        assert _run(tmp_path, prog, **ours) == "16/8"  # the next binary: the whole share
        pinned = {"RUST_TEST_THREADS": "1", "GOH_GATE_WORKERS": "8", "GOH_SHARE_RUST_PINNED": "1"}
        assert _run(tmp_path, prog, **pinned) == "1/8"  # the repo's own count, never re-sized
        split = {"RUST_TEST_THREADS": "2", "GOH_GATE_WORKERS": "8", "GOH_SHARE_DIV": "4"}
        assert _run(tmp_path, prog, **split) == "4/8"  # four binaries at once: a quarter of 16
    finally:
        me.close()


def test_the_admission_names_the_live_runner_unless_the_repo_has_its_own(tmp_path, monkeypatch):
    var = worker_share.cargo_runner_var()
    assert var is not None
    monkeypatch.chdir(tmp_path)
    clean = {"CARGO_HOME": str(tmp_path / "cargo-home")}
    got = worker_share.runner_env(8, clean, mine=0)
    assert got[var].endswith("worker_share.py run"), got
    assert got["GOH_SHARE_OWN"] == "0"
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text("[target.'cfg(unix)']\nrunner = 'x.sh'\n")
    named = worker_share.runner_env(8, clean, mine=0).get(var)
    assert named is None, named
    assert (
        worker_share.runner_env(8, {**clean, "RUST_TEST_THREADS": "1"})["GOH_SHARE_RUST_PINNED"]
        == "1"
    )
    pinned = worker_share.runner_env(8, clean).get("GOH_SHARE_RUST_PINNED")
    assert pinned is None


def test_a_worker_takes_tokens_only_under_xdist_and_starts_nothing_holding_the_pool(tmp_path):
    env = {"GOH_SHARE_POOL": str(tmp_path / "p"), "GOH_SHARE_CEIL": "4"}
    controller = worker_share.tokens_from_env(dict(env))
    assert controller is None  # the controller
    ctl = dict(env)
    worker_share.tokens_from_env(ctl)
    assert "GOH_SHARE_POOL" in ctl  # kept for the workers it starts
    wk = {**env, "PYTEST_XDIST_WORKER": "gw3"}
    tokens = worker_share.tokens_from_env(wk)
    assert tokens is not None and tokens.worker == 3 and tokens.pool.ceiling == 4
    assert "GOH_SHARE_POOL" not in wk  # nothing a test starts takes a token


def test_this_suite_runs_under_its_tokens():
    """The plugin is live in this very run when tools/pytest.sh named a pool."""
    import _share_tokens

    if _share_tokens.TOKENS is None:
        pytest.skip("not an xdist worker of tools/pytest.sh's pooled run")
    assert _share_tokens.TOKENS.held is not None


def test_local_ci_publishes_its_running_steps_to_their_live_share(tmp_path):
    """Two steps at once each see half the gate's live share while both run; the scheduler's
    count file says 0 once they are done."""
    repo = tmp_path / "proj"
    repo.mkdir()
    py = f"{sys.executable} -S {REPO_ROOT / 'lib' / 'worker_share.py'}"

    def step(me: str, peer: str) -> str:
        return (
            f"touch {tmp_path}/{me}.up; n=0; until [ -e {tmp_path}/{peer}.up ] || [ $n -gt 600 ];"
            f" do n=$((n+1)); sleep 0.05; done; {py} live >{tmp_path}/{me};"
            f" touch {tmp_path}/{me}.read; n=0;"
            f" until [ -e {tmp_path}/{peer}.read ] || [ $n -gt 600 ];"
            f" do n=$((n+1)); sleep 0.05; done; echo $GOH_SHARE_STEPS >{tmp_path}/{me}.file"
        )

    (repo / ".gatesrc").write_text(
        f"GOH_CI_STEPS='{step('a', 'b')}:{step('b', 'a')}'\nGOH_CI_JOBS=2\n"
    )
    env = {k: v for k, v in hermetic_env().items() if not k.startswith("GOH_SHARE_")}
    env.update(
        _env(tmp_path), GOH_DIR=str(REPO_ROOT), NO_COLOR="1", GOH_GATE_SLOTS="2", GOH_LIVE="1"
    )
    del env["GOH_SHARE_OWN"]
    other = _hold(tmp_path, "slot-0")
    try:
        r = subprocess.run(
            ["bash", str(REPO_ROOT / "gates" / "local_ci.sh")],
            cwd=repo, env=env, capture_output=True, text=True,
            timeout=patience(120), check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    finally:
        other.close()
    assert r.returncode == 0, r.stdout + r.stderr
    assert [(tmp_path / n).read_text().strip() for n in ("a", "b")] == ["4", "4"]
    count = Path((tmp_path / "a.file").read_text().strip().split(":")[-1])
    assert count.name == "live-steps", count


def test_the_coverage_gate_runs_its_binaries_through_the_live_runner_without_asking_rustc(tmp_path):
    """gates/_cov_run_tests.py starts the test binaries itself through cargo's runner: the live
    runner the admission named is taken as it is -- asking rustc for the target (as a repo's own
    runner needs) failed every coverage run on a PATH with no rustc."""
    sys.path.insert(0, str(REPO_ROOT / "gates"))
    import _cargo_config

    var = worker_share.cargo_runner_var()
    live = {var: f"/py -S {REPO_ROOT}/lib/worker_share.py run"}

    def no_rustc():
        raise AssertionError("rustc asked")

    got = _cargo_config._cargo_runner(tmp_path, live, tmp_path / "home", no_rustc)
    assert got == ["/py", "-S", f"{REPO_ROOT}/lib/worker_share.py", "run"], got


def test_the_live_runner_hands_the_binary_on_to_a_runner_the_repo_names_itself(tmp_path):
    """cargo's runner VARIABLE beats every config file, so the admission's live runner, inherited
    by a nested repo (a fixture inside the land's pre-step, a metarepo's child) that names its own
    runner, ran the binary without it: the pre-step's test_cov_run_tests_runner went red."""
    import shutil

    if shutil.which("rustc") is None:
        pytest.skip("no rustc to name the target")
    repo = tmp_path / "repo"
    (repo / ".cargo").mkdir(parents=True)
    wrap = repo / "wrap.sh"
    wrap.write_text('#!/bin/sh\necho "wrapped"\nexec "$@"\n')
    wrap.chmod(wrap.stat().st_mode | stat.S_IXUSR)
    (repo / ".cargo" / "config.toml").write_text("[target.'cfg(unix)']\nrunner = './wrap.sh'\n")
    prog = _deps_binary(tmp_path)
    var = worker_share.cargo_runner_var()
    env = {**_env(tmp_path), "PATH": os.environ["PATH"], "GOH_LIVE": "1",
           "HOME": str(tmp_path), "CARGO_HOME": str(tmp_path / "cargo-home"),
           "RUST_TEST_THREADS": "8", "GOH_GATE_WORKERS": "8",
           var: f"{sys.executable} -S {REPO_ROOT / 'lib' / 'worker_share.py'} run"}  # fmt: skip
    rustup = Path(os.environ.get("RUSTUP_HOME") or Path.home() / ".rustup")
    env["RUSTUP_HOME"] = str(rustup)
    me = _hold(tmp_path, "slot-1")
    try:
        r = subprocess.run(
            [sys.executable, "-S", str(REPO_ROOT / "lib" / "worker_share.py"), "run", str(prog)],
            cwd=repo, env=env, capture_output=True, text=True,
            timeout=patience(60), check=False, stdin=subprocess.DEVNULL,
        )  # fmt: skip
    finally:
        me.close()
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["wrapped", "16/8"], r.stdout + r.stderr
