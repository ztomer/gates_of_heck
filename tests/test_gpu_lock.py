"""gpu_lock -- one lock, two halves here (bash, Python) and a third in ztools (Rust).

Every test points ZTOOLS_GPU_LOCK_DIR at a scratch path, so the suite never touches the
machine's real GPU lock, which a model run in another session may be holding. The cross-
language tests are the point of the file: a Python half that read the bash half's records as
impostors would grant a GPU a live peer holds, and say "acquired" while doing it.
"""

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest
from test_bench_lock import _quiet

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = REPO_ROOT / "lib" / "gpu_lock" / "gpu_lock.py"
SHELL = REPO_ROOT / "lib" / "gpu_lock" / "gpu_lock.sh"


@pytest.fixture
def lock(tmp_path, monkeypatch):
    """The module, its lock directory a throwaway path and no inherited owner."""
    monkeypatch.setenv("ZTOOLS_GPU_LOCK_DIR", str(tmp_path / "gpu.lock"))
    monkeypatch.delenv("ZTOOLS_GPU_LOCK_OWNER", raising=False)
    spec = importlib.util.spec_from_file_location("gpu_lock", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    yield mod
    mod.release()


def _plant(lock, pid, start, label="peer eval"):
    os.mkdir(lock.lock_dir())
    Path(lock.lock_dir(), "owner").write_text(f"{pid}\n{start}\n{label}\n")


@pytest.fixture
def peer():
    """A live process that is not this one, to stand in for another session's holder."""
    proc = subprocess.Popen(["sleep", "60"], stdin=subprocess.DEVNULL)
    yield proc
    proc.kill()
    proc.wait()


def test_the_owner_record_is_pid_start_label_and_the_owner_is_exported(lock):
    lock.acquire("census S6", log=lambda _: None)
    pid = str(os.getpid())
    assert Path(lock.lock_dir(), "owner").read_text().splitlines() == [
        pid,
        lock.start_time(pid),
        f"census S6 (pid {pid})",
    ]
    assert os.environ["ZTOOLS_GPU_LOCK_OWNER"] == pid
    lock.release()
    assert not os.path.exists(lock.lock_dir())
    assert "ZTOOLS_GPU_LOCK_OWNER" not in os.environ


def test_a_live_beating_peer_is_refused_by_name_never_stolen(lock, peer):
    _plant(lock, peer.pid, lock.start_time(peer.pid))
    with pytest.raises(lock.GpuBusy, match="peer eval"):
        lock.acquire(timeout=2, log=lambda _: None, nap=lambda _: None)
    assert Path(lock.lock_dir(), "owner").read_text().startswith(f"{peer.pid}\n")
    assert lock.holder() == "peer eval" and lock.foreign_holder() == "peer eval"


def test_a_dead_owner_is_reclaimed(lock, peer):
    pid = peer.pid
    peer.kill()
    peer.wait()
    _plant(lock, pid, "Mon Jan  1 00:00:00 2024")
    lock.acquire(log=lambda _: None, nap=lambda _: pytest.fail("waited on a dead owner"))
    assert lock.holder().endswith(f"(pid {os.getpid()})")


def test_a_recycled_pid_does_not_impersonate_the_owner(lock, peer):
    _plant(lock, peer.pid, "Mon Jan  1 00:00:00 2024")
    lock.acquire(log=lambda _: None, nap=lambda _: pytest.fail("waited on an impostor"))
    assert lock.foreign_holder() == ""


def test_a_holder_that_stopped_beating_is_reclaimed_and_one_that_beats_is_not(lock, peer):
    _plant(lock, peer.pid, lock.start_time(peer.pid))
    long_ago = time.time() - 100
    os.utime(lock.lock_dir(), (long_ago, long_ago))
    with pytest.raises(lock.GpuBusy):
        lock.acquire(timeout=0, max_idle=1000, log=lambda _: None, nap=lambda _: None)
    lock.acquire(timeout=0, max_idle=50, log=lambda _: None, nap=lambda _: None)
    assert lock.holder().endswith(f"(pid {os.getpid()})")


def test_a_heartbeat_moves_the_ceiling_only_for_the_holder(lock, peer):
    _plant(lock, peer.pid, lock.start_time(peer.pid))
    os.utime(lock.lock_dir(), (1, 1))
    lock.heartbeat()
    assert os.stat(lock.lock_dir()).st_mtime == 1, "an unheld caller kept a peer's lock alive"


def test_the_holders_heartbeat_moves_the_ceiling(lock):
    lock.acquire(log=lambda _: None)
    os.utime(lock.lock_dir(), (1, 1))
    lock.heartbeat()
    assert os.stat(lock.lock_dir()).st_mtime > 1


def test_a_child_inherits_its_parents_lock_and_does_not_release_it(lock):
    lock.acquire("parent", log=lambda _: None)
    child = subprocess.run(
        [
            "python3",
            "-c",
            (
                "import importlib.util,sys;s=importlib.util.spec_from_file_location('g',sys.argv[1]);"
                "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                "m.acquire('child',timeout=0,log=print,nap=lambda _:None);m.release()"
            ),
            str(MODULE),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child.returncode == 0, child.stderr
    assert "inherited" in child.stdout
    assert lock.holder().startswith("parent"), "the child released its parent's lock"


def _bash(script, env):
    return subprocess.run(
        ["bash", "-c", f'. "{SHELL}"; {script}'],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_both_halves_spell_a_start_time_byte_for_byte(lock, peer):
    shell = _bash(f"_gpu_lock_start_time {peer.pid}", dict(os.environ))
    assert shell.stdout == lock.start_time(peer.pid) != ""


def test_each_half_refuses_the_other_s_live_lock(lock):
    lock.acquire("python holder", log=lambda _: None)
    # A PEER, not a child: without the owner variable the bash half cannot inherit.
    env = {k: v for k, v in os.environ.items() if k != "ZTOOLS_GPU_LOCK_OWNER"}
    env["GPU_LOCK_TIMEOUT"] = "0"
    shell = _bash('gpu_lock_acquire "bash eval"', env)
    assert shell.returncode != 0 and "python holder" in shell.stdout + shell.stderr
    lock.release()

    holder = subprocess.Popen(
        ["bash", "-c", f'. "{SHELL}"; gpu_lock_acquire "bash eval"; echo held; sleep 30'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(lock.GpuBusy, match="bash eval"):
            lock.acquire(timeout=0, log=lambda _: None, nap=lambda _: None)
    finally:
        holder.kill()
        holder.wait()


PADDED = "  Thu Oct  2 09:05:07 2026  \n"  # `ps` pads a single-digit day: two days in three


def test_both_halves_collapse_a_padded_day_the_same_way(lock, monkeypatch):
    """The live-pid parity above cannot see this on a two-digit day, so both halves are fed
    `ps`'s padded spelling: the bash half through a shadowing `ps` function, the Python one
    through its subprocess call."""
    shell = _bash(
        f"ps() {{ printf '{PADDED.rstrip()}\\n'; }}; _gpu_lock_start_time 1", dict(os.environ)
    )

    class Done:
        stdout = PADDED

    monkeypatch.setattr(lock.subprocess, "run", lambda *a, **k: Done())
    assert shell.stdout == lock.start_time(1) == "Thu Oct 2 09:05:07 2026"


def test_quiet_refuses_a_gpu_another_session_holds_and_names_it(tmp_path) -> None:
    """A model run holds the GPU for hours and loads the box under every wall time: quiet.sh
    names it and refuses rather than measuring beside it, and leaves the holder's lock alone."""
    gpu = tmp_path / "gpu.lock"
    held = (f'. "{SHELL}"; gpu_lock_acquire "a model eval"; '
            f'echo held; sleep 30')  # fmt: skip
    holder = subprocess.Popen(["bash", "-c", held], env={**os.environ, "ZTOOLS_GPU_LOCK_DIR": str(gpu)},
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True)  # fmt: skip
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        r = _quiet(tmp_path, "--max-load", "1000", "--", "true", GPU_LOCK_TIMEOUT="0")
        assert r.returncode != 0 and "a model eval" in r.stdout + r.stderr, r.stdout + r.stderr
        assert (gpu / "owner").exists(), "quiet.sh released a lock it never held"
    finally:
        holder.kill()
        holder.wait()
