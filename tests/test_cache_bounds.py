"""Two caches with no real bound (GOH.CACHEBOUND, measured 2026-11-13).

* `~/.cache/goh/llvm-cov` held 21 GiB in 43 entries against a default 8 GiB budget: a rust
  coverage sweep builds ~40 projects in an hour and the byte budget spared EVERY entry used within
  the hour ("a gate may be running from it"), so a burst was never evicted. The bound is now
  liveness: a run writes `.goh-running` (its pid), and only an entry whose pid is alive (or one
  stamped within a short grace) is spared. A bound that holds only when nobody uses the machine is
  not a bound.
* `~/.cache/goh/push/ztools-*` held 16 GiB, all of it one private sccache under
  `rust/target/test-home`: the repo's test sandbox sets HOME, and an sccache server STARTED by a
  test inherits that HOME, so its default cache dir (and every later build the server served) was
  private to the sandbox. The gates now pin SCCACHE_DIR to the user's before any repo code runs
  (`lib/sccache_dir.sh`). And the export dir that server's writes kept alive, with no `.owner`,
  was neither reaped nor reusable: a push found its stable path "held by a live push" and built
  cold into a private one. An ownerless run dir idle past a grace is a dead run's.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from conftest import hermetic_env
from test_push_gate import GATE, REPO_ROOT, _git, _repo
from test_push_gate_runs import _push, _run_litter

sys.path.insert(0, str(REPO_ROOT / "gates"))
import _head_cache

HOUR = 3600


def _entry(cache: Path, name: str, used_ago: float, mib: int = 1, pid: int | None = None) -> Path:
    d = cache / name
    d.mkdir(parents=True)
    (d / "blob").write_bytes(b"\1" * (mib << 20))
    then = time.time() - used_ago
    (d / ".goh-used").write_text("")
    os.utime(d / ".goh-used", (then, then))
    if pid is not None:
        (d / ".goh-running").write_text(f"{pid}\n")
    return d


def _dead_pid() -> int:
    proc = subprocess.Popen(["/usr/bin/true"], stdin=subprocess.DEVNULL)
    proc.wait()
    return proc.pid


# ── llvm-cov: the byte budget holds in a burst ────────────────────────────────────────────────


def test_a_burst_of_idle_builds_is_evicted_to_the_budget(tmp_path) -> None:
    """Ten builds, all used within the last hour and none running: the old rule spared all ten."""
    cache = tmp_path / "cov"
    _entry(cache, "cur", 0)
    for i in range(10):
        _entry(cache, f"b{i}", (20 + i) * 60)  # 20..29 minutes ago, b0 most recent
    gone = _head_cache.prune(str(cache), "cur", keep=200, max_bytes=3 << 20, grace=600)
    left = {p.name for p in cache.iterdir()}
    assert left == {"cur", "b0", "b1"}, (sorted(left), gone)


def test_a_build_whose_gate_is_alive_is_never_evicted(tmp_path) -> None:
    cache = tmp_path / "cov"
    _entry(cache, "cur", 0)
    _entry(cache, "running", 5 * HOUR, pid=os.getpid())
    _entry(cache, "crashed", 5 * HOUR, pid=_dead_pid())
    _head_cache.prune(str(cache), "cur", keep=200, max_bytes=1, grace=600, marker=".goh-running")
    left = {p.name for p in cache.iterdir()}
    assert left == {"cur", "running"}, sorted(left)


def test_a_build_stamped_inside_the_grace_is_spared(tmp_path) -> None:
    """A run from before the marker existed (or between its stamp and its marker) is not lost."""
    cache = tmp_path / "cov"
    _entry(cache, "cur", 0)
    _entry(cache, "fresh", 60)
    _head_cache.prune(str(cache), "cur", keep=200, max_bytes=1, grace=600, marker=".goh-running")
    assert (cache / "fresh").is_dir()


def test_the_default_grace_is_unchanged_for_the_head_exports(tmp_path) -> None:
    """The HEAD export cache keeps its hour: nothing there writes a marker."""
    cache = tmp_path / "head"
    _entry(cache, "cur", 0)
    _entry(cache, "hot", 20 * 60)
    _head_cache.prune(str(cache), "cur", keep=200, max_bytes=1)
    assert (cache / "hot").is_dir()


def test_the_coverage_gate_marks_its_build_as_running_and_prunes_the_idle(tmp_path) -> None:
    cache = tmp_path / "cov-cache"
    idle = _entry(cache, "0123456789abcdef", 20 * 60, mib=2)  # inside the old hour, not running
    crate = tmp_path / "proj"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "proj"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (crate / "src" / "lib.rs").write_text("pub fn one() -> u32 {\n    1\n}\n")
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
         "--floor", "0", str(crate)],
        cwd=crate, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_COV_CACHE=str(cache), GOH_COV_CACHE_MAX_GIB="0.001"), check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert not idle.exists(), "an idle build inside the hour stayed past the budget"
    (own,) = list(cache.iterdir())
    pid = int((own / ".goh-running").read_text().split()[0])
    assert pid > 0, "the run did not record who is using its build"


# ── sccache: a test sandbox's HOME never moves the compile cache ──────────────────────────────


def _default_sccache(home: str) -> str:
    if sys.platform == "darwin":
        return f"{home}/Library/Caches/Mozilla.sccache"
    return f"{home}/.cache/sccache"


def _pinned(home: str, sccache_dir: str | None, then_home: str) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": home}
    if sccache_dir is not None:
        env["SCCACHE_DIR"] = sccache_dir
    script = (
        f'. "{REPO_ROOT}/lib/sccache_dir.sh"; HOME="{then_home}"; env | sed -n "s/^SCCACHE_DIR=//p"'
    )
    out = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return out.stdout.strip()


def test_sourcing_pins_the_users_sccache_dir_before_a_sandbox_moves_home() -> None:
    assert _pinned("/u/home", None, "/u/home/rust/target/test-home") == _default_sccache("/u/home")


def test_a_dir_the_user_chose_is_kept() -> None:
    assert _pinned("/u/home", "/fast/disk/sccache", "/x") == "/fast/disk/sccache"


def test_the_push_gate_hands_the_gate_the_pinned_dir(tmp_path) -> None:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        GATE.replace(
            "exit 0\n", 'printf "sccache=%s\\n" "${SCCACHE_DIR-UNSET}" >> "$GATE_REPORT"\nexit 0\n'
        )
    )
    _git(repo, "commit", "-qam", "a gate that reports its sccache dir")
    home = tmp_path / "home"
    home.mkdir()
    sha = _git(repo, "rev-parse", "HEAD")
    env = {k: v for k, v in os.environ.items() if k != "SCCACHE_DIR"}
    env.update(
        HOME=str(home), GATE_REPORT=str(tmp_path / "report.txt"), GOH_DIR=str(REPO_ROOT),
        GOH_PUSH_LOGS=str(tmp_path / "logs"), GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
    )  # fmt: skip
    got = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "push_gate.sh")], cwd=repo, env=env, text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n", capture_output=True,
        timeout=60, check=False,
    )  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    report = (tmp_path / "report.txt").read_text()
    assert f"sccache={_default_sccache(str(home))}" in report, report


def test_every_gate_runner_pins_it_through_the_common_contract() -> None:
    text = (REPO_ROOT / "gates" / "_common.sh").read_text()
    assert "lib/sccache_dir.sh" in text


# ── the push export root: an ownerless run dir is a dead run's ────────────────────────────────


def _ownerless(export_root: Path, name: str, age_s: float) -> Path:
    """What a sccache server left: a stable run dir recreated by a stray writer, no `.owner`."""
    leak = export_root / name / "ztools" / "rust" / "target" / "test-home"
    leak.mkdir(parents=True)
    (leak / "blob").write_bytes(b"\1" * 1024)
    then = time.time() - age_s
    os.utime(export_root / name, (then, then))
    return export_root / name


def test_an_ownerless_run_dir_idle_past_the_grace_is_reaped(tmp_path) -> None:
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    stale = _ownerless(export_root, "ztools-aa142a95d609", 3 * HOUR)
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert not stale.exists(), "an ownerless run dir survived the next push"
    assert _run_litter(export_root) == []


def test_a_fresh_ownerless_run_dir_is_left_alone(tmp_path) -> None:
    """The window between a run's mkdir and its `.owner`: another push must not delete it."""
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    fresh = _ownerless(export_root, "claimed-just-now", 5)
    assert _push(repo, tmp_path, export_root).returncode == 0
    assert fresh.is_dir()


def test_a_stale_ownerless_dir_does_not_make_the_stable_path_look_held(tmp_path) -> None:
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    name = f"repo-{__import__('hashlib').sha256(str(repo.resolve()).encode()).hexdigest()[:12]}"
    _ownerless(export_root, name, 3 * HOUR)
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "held by a live push" not in got.stdout + got.stderr
