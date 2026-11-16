"""The native binary is judged against the COMMITTED version, not the shared checkout's working tree.

2026-10-05: a session bumped `Cargo.toml` to 0.20.0 in the shared checkout, uncommitted, ahead of the
release commit -- and every consumer's pre-commit refused every commit ("the goh binary serving this
gate is BEHIND the source"), a private Swift app's among them. The check exists for a real hazard --
a step COMMITTED without a rebuild runs nowhere -- and that is a fact about HEAD. Uncommitted gate
source is already named, by path, by its own warning; it must not also stop the estate.
"""

import subprocess
from pathlib import Path

from conftest import hermetic_env
from test_gate_environment import _clean_checkout_of_todays_gates, _git


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


def _head_version(checkout: Path) -> str:
    """The version a build of the checkout's HEAD reports: its tag (lib/goh_version.sh)."""
    return subprocess.run(
        ["bash", str(checkout / "lib" / "goh_version.sh"), str(checkout), "HEAD"],
        capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip


def _consumer_env(checkout: Path, fake: Path, tmp_path: Path) -> dict:
    """A CONSUMER's environment: no GOH_LIVE (the suite sets it for itself; a consumer never does),
    no PYTEST_* (a gate under pytest without GOH_LIVE refuses), its own HEAD-export cache."""
    env = {k: v for k, v in hermetic_env().items() if not k.startswith(("GOH_LIVE", "PYTEST_"))}
    env.update(GOH_DIR=str(checkout), GOH_BIN=str(fake), GOH_HEAD_CACHE=str(tmp_path / "heads"))
    return env


def _structural(checkout: Path, fake: Path, tmp_path: Path):
    repo = tmp_path / "consumer"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, stdin=subprocess.DEVNULL)
    return subprocess.run(
        ["bash", str(checkout / "gates" / "structural.sh"), "--staged"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=_consumer_env(checkout, fake, tmp_path),
        timeout=120,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_an_uncommitted_version_bump_does_not_stop_the_estate(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake_goh(tmp_path, _head_version(checkout))
    _bump(checkout, "99.0.0")  # uncommitted, in the shared checkout
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "fake native structural ran" in got.stdout, got.stdout + got.stderr


def test_a_committed_cargo_version_is_not_the_version(tmp_path):
    """GOH.SHIPRELREUSE: the version is the tag. Cargo.toml's is a placeholder a release does not
    touch, so a commit that changes it changes nothing a binary must report."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake_goh(tmp_path, _head_version(checkout))
    _bump(checkout, "99.0.0")
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "bump")
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode == 0, got.stdout + got.stderr


def test_a_release_tag_the_binary_is_behind_is_refused(tmp_path):
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "-m", "release")  # fmt: skip
    fake = _fake_goh(tmp_path, _head_version(checkout))
    _git(checkout, "tag", "v99.0.0")
    assert _head_version(checkout) == "99.0.0"
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode != 0, got.stdout + got.stderr
    assert "BEHIND" in got.stdout + got.stderr and "99.0.0" in got.stdout + got.stderr


def test_a_build_of_another_commit_past_the_same_release_serves(tmp_path):
    """One build serves every commit with its source (the stamp decides which, gates/_goh_bin.sh):
    a binary built at an earlier commit past the same tag says that commit, and is current."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    # PAST a release, whatever the checkout it was cloned from has on HEAD: on a release commit
    # its own build must say the release, so this test read a tagged checkout as stale
    # (goh's CI on every release commit; GOH.PROVENTAGHEAD).
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
         "-m", "past the release")  # fmt: skip
    want = _head_version(checkout)
    assert "-" in want or want == "unknown", want  # calibration: HEAD is on no release
    base = want.split("-")[0]
    fake = _fake_goh(tmp_path, f"{base}-1-g0000000000")
    got = _structural(checkout, fake, tmp_path)
    assert got.returncode == 0, got.stdout + got.stderr


def test_a_binary_ahead_of_the_source_is_refused_and_called_ahead(tmp_path):
    """2026-10-17: a land rebuilt bin/goh (0.29.0) under a gate pinned to the commit before
    (0.28.1), and the refusal said "BEHIND" -- sending the reader after a rebuild that was never
    missing. Still refused (the binary is not this source's build), but named the right way."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    fake = _fake_goh(tmp_path, "99.0.0")
    got = _structural(checkout, fake, tmp_path)
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert "is AHEAD of the source" in text and "BEHIND" not in text, text
