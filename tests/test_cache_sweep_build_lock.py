"""A build dir a running cargo holds is never demoted or deleted (GOH.BUILDDIRVANISH, 2026-11-22).

A release's cargo build failed with `extern location for pastey does not exist` under
`~/.cargo/build/ff/ce89afd4bc0c10`: the evictor's 03:02:55Z sweep (goh-cache-sweep.log) DEMOTED
that dir -- copied it to the large tier and removed it -- mid-build. The sweep skips a dir whose
cargo lock it cannot take, but it looked for `<profile>/.cargo-lock`, which with a separate
`build-dir` lives in the TARGET dir: cargo 1.100 holds `<build-dir>/<profile>/.cargo-build-lock`
in the build dir (probed), so it found no lock and moved a dir in use. The first tests plant that
layout; the last runs a real cargo, so a lock renamed again is caught by the instrument itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import cache_evict as ce
import cache_sweep
import cache_tier
from test_cache_sweep import NOW, _drive, _roots
from timing_bounds import GiveUp, patience


def _bd_1100(root, name, age_h):
    """A cold build dir in cargo 1.100's layout: the lock is `.cargo-build-lock`, nothing else."""
    d = root / "build" / name[:2] / name
    (d / "debug" / "deps").mkdir(parents=True)
    (d / "debug" / "deps" / "liba-0.rlib").write_bytes(b"r" * 100)
    (d / "debug" / ".cargo-build-lock").write_text("")
    t = NOW - age_h * 3600
    for p in [d / "debug" / "deps", d / "debug" / ".cargo-build-lock", d / "debug", d]:
        os.utime(p, (t, t))
    return d


def test_the_build_lock_is_one_of_the_dirs_cargo_locks(tmp_path):
    d = _bd_1100(tmp_path, "aa11", 72)
    assert str(d / "debug" / ".cargo-build-lock") in ce.cargo_locks(str(d))


@pytest.mark.parametrize("drive", [True, False], ids=["demote", "delete"])
def test_a_held_build_lock_keeps_the_dir_through_a_real_sweep(tmp_path, drive):
    cold, _hot = _bd_1100(tmp_path, "aa11", 72), _bd_1100(tmp_path, "bb22", 2)
    if drive:
        _root, v, opener = _drive(tmp_path)
    else:
        v, opener = cache_tier.Verdict(str(tmp_path / "int"), False, "absent"), None
    roots = _roots(tmp_path, free_gib=10)
    with open(cold / "debug" / ".cargo-build-lock") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        kw = {"opener": opener} if opener else {}
        report = cache_sweep.sweep(roots, v, str(tmp_path / "s.json"), NOW, True, **kw)
    assert (cold / "debug" / "deps" / "liba-0.rlib").exists(), report["done"]
    assert not [d for d in report["done"] if d["key"] == str(cold) and d["ok"]], report["done"]


@pytest.mark.load_sensitive
@pytest.mark.skipif(shutil.which("cargo") is None, reason="no cargo on PATH")
def test_a_sweep_during_a_real_cargo_build_leaves_its_build_dir(tmp_path):
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "Cargo.toml").write_text('[package]\nname = "probe"\nversion = "0.1.0"\n'
                                     'edition = "2021"\nbuild = "build.rs"\n')  # fmt: skip
    (proj / "src" / "main.rs").write_text("fn main() {}\n")
    gate, started = tmp_path / "go", tmp_path / "started"
    (proj / "build.rs").write_text(
        f'fn main() {{ std::fs::write({json.dumps(str(started))}, b"").unwrap();'
        f" while !std::path::Path::new({json.dumps(str(gate))}).exists() {{"
        " std::thread::sleep(std::time::Duration::from_millis(50)); } }\n"
    )
    bd = tmp_path / "build" / "cd" / "cd34ef56"
    argv = ["cargo", "build", "-q", "--config", f"build.build-dir={json.dumps(str(bd))}",
            "--target-dir", str(tmp_path / "target")]  # fmt: skip
    build = subprocess.Popen(argv, cwd=proj, env=hermetic_env(RUSTC_WRAPPER=""), text=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)  # fmt: skip
    try:
        give_up = GiveUp(120)
        while not started.exists() and not give_up.passed() and build.poll() is None:
            time.sleep(0.1)  # a real cargo reaching its build script
        assert build.poll() is None, build.stderr.read() if build.stderr else ""
        old = NOW - 72 * 3600
        for p in [bd, *bd.rglob("*")]:
            os.utime(p, (old, old), follow_symlinks=False)
        v = cache_tier.Verdict(str(tmp_path / "int"), False, "absent")
        report = cache_sweep.sweep(_roots(tmp_path, free_gib=10), v, str(tmp_path / "s.json"),
                                   NOW, True)  # fmt: skip
        assert bd.is_dir(), f"the sweep removed a dir cargo was building in: {report['done']}"
    finally:
        gate.write_text("")
        _out, err = build.communicate(timeout=patience(300))
    assert build.returncode == 0, err


def test_a_removed_worktrees_reap_keeps_a_build_dir_a_cargo_holds(tmp_path):
    import build_dir_reap

    held, free = _bd_1100(tmp_path, "aa11", 1), _bd_1100(tmp_path, "bb22", 1)
    with open(held / "debug" / ".cargo-build-lock") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        kept = build_dir_reap.remove([held, free, tmp_path / "build" / "cc" / "cc33"])
    assert kept == [held] and (held / "debug" / "deps").is_dir() and not free.exists()
