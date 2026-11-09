"""A Cargo.lock behind its newest compatible releases fails the push (GOH.CRATESBEHIND).

House Rust rule 2 is "latest dependencies", and nothing read it: routines' lock had fallen 37
compatible updates behind (FAULTS.ROUTINES) with every gate green. `goh crates-behind` runs
`cargo update --dry-run` over each lock the gate names; a lock it would move is red, naming each
crate. Calibrated on a fixture held one patch release back (itoa 1.0.18, newest 1.0.19 on the day
this landed): red, then green after `cargo update`. That arm needs the registry, so it skips --
saying so -- when the index cannot be reached; the offline arm is hermetic.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, run_goh
from test_rust_gate import CURRENT, LIB_CLEAN, _git, mkcrate, run_script

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")

ENV = {**os.environ, "GOH_DIR": str(REPO_ROOT)}
ENV.pop("GOH_DEPS_OFFLINE", None)
HELD = "1.0.18"  # one patch below itoa's newest 1.x on 2026-11-09


def _held_back(tmp: Path) -> Path:
    """A crate whose lock holds `itoa` one patch release behind, committed."""
    (tmp / "src").mkdir(parents=True)
    (tmp / "Cargo.toml").write_text(
        '[package]\nname = "held"\nversion = "0.1.0"\nedition = "2024"\n\n'
        '[workspace]\n\n[dependencies]\nitoa = "1"\n'
    )
    (tmp / "src" / "lib.rs").write_text("")
    for argv in (
        ["cargo", "generate-lockfile", "-q"],
        ["cargo", "update", "-q", "-p", "itoa", "--precise", HELD],
    ):
        r = subprocess.run(
            argv, cwd=tmp, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
        )
        if r.returncode != 0:
            pytest.skip(f"registry unreachable ({' '.join(argv)}): {r.stderr.strip()[:200]}")
    _git(tmp, "init", "-q")
    _git(tmp, "add", "-A")
    _git(tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "fixture")
    return tmp


def test_a_lock_held_one_patch_back_is_red_until_cargo_update(tmp_path):
    crate = _held_back(tmp_path)
    r = run_goh(crate, "crates-behind", "--root", str(crate), env=ENV)
    if "NOT judged" in r.stderr:
        pytest.skip(f"registry index unreachable: {r.stderr.strip()[:200]}")
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"Cargo.lock: itoa v{HELD} -> v" in r.stderr, r.stderr
    assert "cargo update" in r.stderr, "the fix is named"
    assert (
        "itoa" in (crate / "Cargo.lock").read_text() and HELD in (crate / "Cargo.lock").read_text()
    ), "a dry run writes nothing"
    subprocess.run(
        ["cargo", "update", "-q"],
        cwd=crate,
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    g = run_goh(crate, "crates-behind", "--root", str(crate), env=ENV)
    assert g.returncode == 0, g.stdout + g.stderr
    assert "every Cargo.lock is at its newest compatible releases" in g.stdout, g.stdout


def test_offline_is_a_stated_skip_never_a_clean_bill(tmp_path):
    crate = mkcrate(tmp_path)
    r = run_goh(crate, "crates-behind", "--root", str(crate), env={**ENV, "GOH_DEPS_OFFLINE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT judged -- offline" in r.stderr, r.stderr
    assert "NOT a clean bill" in r.stdout and "every Cargo.lock is at" not in r.stdout, r.stdout


def test_a_lock_that_cannot_be_judged_is_not_a_pass(tmp_path):
    crate = mkcrate(tmp_path)
    r = run_goh(crate, "crates-behind", "--root", str(crate), "--lock", "gone/Cargo.lock", env=ENV)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "cannot judge -- no lock at" in r.stderr and r.stdout == "", r.stdout + r.stderr


def test_the_rust_gate_runs_it_over_the_repos_locks(tmp_path):
    crate = mkcrate(tmp_path)
    (crate / "src" / "lib.rs").write_text(LIB_CLEAN)
    r = run_script(CURRENT, crate, {"GOH_DEPS_OFFLINE": "1", "GOH_PROVEN": "0"})
    out = r.stdout + r.stderr
    assert "every Cargo.lock at its newest compatible releases (cargo update --dry-run)" in out, out
    assert "Cargo.lock: NOT judged -- offline" in out, out
