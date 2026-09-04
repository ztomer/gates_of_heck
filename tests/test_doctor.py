"""gates/doctor.sh — wiring diagnosis.

Red-proofed: a healthy wired repo exits 0; each broken layer (missing
gate entry, unset hooksPath, unknown .gatesrc key, non-repo) is exercised
and names itself. Unknown-key detection derives from docs/config.md, so a
new key needs no doctor change (same single-source rule as the schema
drift gate)."""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, git, write

DOCTOR = REPO_ROOT / "gates" / "doctor.sh"


def run_doctor(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(DOCTOR), str(target)],
        capture_output=True, text=True,
    )


def _wire(repo: Path) -> None:
    (repo / ".gatesrc").write_text("GOH_MAX_LINES=500\n")
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_healthy_wired_repo_passes(repo):
    _wire(repo)
    r = run_doctor(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "healthy" in (r.stdout + r.stderr).lower()


def test_missing_gate_entry_fails_named(repo):
    _wire(repo)
    (repo / "tools" / "gate.sh").unlink()
    r = run_doctor(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tools/gate.sh" in (r.stdout + r.stderr)


def test_unset_hookspath_fails_named(repo):
    _wire(repo)
    subprocess.run(
        ["git", "-C", str(repo), "config", "--unset", "core.hooksPath"],
        check=True,
    )
    r = run_doctor(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "core.hooksPath" in (r.stdout + r.stderr)


def test_unknown_gatesrc_key_warns_but_passes(repo):
    _wire(repo)
    (repo / ".gatesrc").write_text("GOH_MAX_LINES=500\nGOH_BOGUS_KEY=1\n")
    r = run_doctor(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GOH_BOGUS_KEY" in (r.stdout + r.stderr)


def test_all_known_keys_pass_silently_on_that_point(repo):
    _wire(repo)
    (repo / ".gatesrc").write_text(
        "GOH_MAX_LINES=500\nGOH_EXCLUDE='x'\nGOH_CI_STEPS='true'\n")
    r = run_doctor(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BOGUS" not in (r.stdout + r.stderr)
    assert "unknown" not in (r.stdout + r.stderr).lower()


def test_non_repo_dir_fails_named(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = run_doctor(plain)
    assert r.returncode != 0
    assert "not a git repo" in (r.stdout + r.stderr)


def test_missing_python_is_named(tmp_path):
    # A bin dir with git but WITHOUT python3: exercises the load-bearing
    # check without touching the machine.
    import shutil as _shutil
    import os as _os
    target = tmp_path / "proj"
    target.mkdir()
    git(target, "init", "-q", "-b", "main")
    write(target, "f.txt", "x\n")
    bindir = tmp_path / "nopython"
    bindir.mkdir()
    _os.symlink(_shutil.which("git"), bindir / "git")
    r = subprocess.run(
        ["/bin/bash", str(DOCTOR), str(target)],
        capture_output=True, text=True,
        env={"PATH": str(bindir), "HOME": str(tmp_path)},
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "python3" in (r.stdout + r.stderr)


def test_gate_help_flag_prints_usage(tmp_path):
    import os as _os

    env = dict(_os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "tools" / "gate.sh"), "--help"],
        capture_output=True, text=True, cwd=tmp_path, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "--staged" in combined and "--full" in combined


def test_gate_doctor_flag_delegates(repo):
    import os as _os

    (repo / ".gatesrc").write_text("GOH_MAX_LINES=500\n")
    subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "install.sh"), str(repo)],
        capture_output=True, text=True, check=True,
    )
    env = dict(_os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "tools" / "gate.sh"),
         "--doctor", str(repo)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "healthy" in (r.stdout + r.stderr).lower()
