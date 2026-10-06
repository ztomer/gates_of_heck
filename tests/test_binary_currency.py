"""The native binary is judged against the COMMITTED version, not the shared checkout's working tree.

2026-10-05: a session bumped `Cargo.toml` to 0.20.0 in the shared checkout, uncommitted, ahead of the
release commit -- and every consumer's pre-commit refused every commit ("the goh binary serving this
gate is BEHIND the source"), ZoneWM's among them. The check exists for a real hazard -- a step
COMMITTED without a rebuild runs nowhere -- and that is a fact about HEAD. Uncommitted gate source is
already named, by path, by its own warning; it must not also stop the estate.
"""

import subprocess
from pathlib import Path

from test_gate_environment import _clean_checkout_of_todays_gates, _git
from conftest import hermetic_env


def _fake_goh(tmp_path: Path, version: str) -> Path:
    fake = tmp_path / "fake-goh"
    fake.write_text(
        f'#!/bin/bash\n[ "$1" = --version ] && {{ echo "goh {version}"; exit 0; }}\n'
        'echo "fake native structural ran"\nexit 0\n'
    )
    fake.chmod(0o755)
    return fake


def _bump(checkout: Path, to: str) -> None:
    manifest = checkout / "Cargo.toml"
    text = manifest.read_text()
    current = next(l for l in text.splitlines() if l.startswith("version = "))
    manifest.write_text(text.replace(current, f'version = "{to}"', 1))


def _committed_version(checkout: Path) -> str:
    line = next(
        l for l in (checkout / "Cargo.toml").read_text().splitlines() if l.startswith("version = ")
    )
    return line.split('"')[1]


def _structural(checkout: Path, fake: Path, tmp_path: Path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return subprocess.run(
        ["bash", str(checkout / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(checkout), GOH_BIN=str(fake)),
        timeout=120,
    )


def test_an_uncommitted_version_bump_does_not_stop_the_estate(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake_goh(tmp_path, _committed_version(checkout))
    _bump(checkout, "99.0.0")  # uncommitted, in the shared checkout
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "fake native structural ran" in got.stdout, got.stdout + got.stderr


def test_a_committed_version_the_binary_is_behind_is_refused(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake_goh(tmp_path, _committed_version(checkout))
    _bump(checkout, "99.0.0")
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "release")
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode != 0, got.stdout + got.stderr
    assert "BEHIND" in got.stdout + got.stderr and "99.0.0" in got.stdout + got.stderr
