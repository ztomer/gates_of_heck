"""gates/goh.sh is the ONE way to run a house checker, and _goh_bin.sh the one binary resolution.

The resolution was written three times and had drifted (the lints copy replaced a named-but-broken
GOH_BIN with bin/goh). These pin the entry point's four outcomes and forbid a fourth copy.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "gates" / "goh.sh"


def run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {k: v for k, v in os.environ.items() if k not in ("GOH_BIN", "GOH_NO_NATIVE")}
    return subprocess.run(["bash", str(ENTRY), *args], capture_output=True, text=True, cwd=ROOT, env={**base, **env})


def fake_native(tmp_path: Path) -> Path:
    fake = tmp_path / "goh"
    fake.write_text('#!/bin/sh\necho "native $*"\n')
    fake.chmod(0o755)
    return fake


def test_a_resolved_binary_runs_with_the_arguments_as_given(tmp_path: Path) -> None:
    r = run("home-paths", "--staged", "--exclude", "^x/", GOH_BIN=str(fake_native(tmp_path)))
    assert r.returncode == 0
    assert r.stdout.strip() == "native home-paths --staged --exclude ^x/"


def test_a_named_binary_that_is_not_executable_is_reported_and_python_runs(tmp_path: Path) -> None:
    r = run("markers", "--staged", GOH_BIN=str(tmp_path / "missing"))
    assert r.stderr.count("is not an executable") == 1, r.stderr
    assert "no_conflict_markers" in r.stdout, "the Python checker ran — not some other binary"


def test_no_native_runs_python_without_a_word(tmp_path: Path) -> None:
    r = run("markers", "--staged", GOH_NO_NATIVE="1", GOH_BIN=str(fake_native(tmp_path)))
    assert "native" not in r.stdout
    assert r.stderr == ""


def test_an_argument_only_python_takes_runs_python(tmp_path: Path) -> None:
    r = run("lints", "--self-test", GOH_BIN=str(fake_native(tmp_path)))
    assert "native" not in r.stdout
    assert "Python-only" in r.stderr


def test_an_unknown_check_is_refused() -> None:
    assert run("frobnicate").returncode == 2


def test_no_gate_script_resolves_the_binary_itself() -> None:
    """THE GATE: one resolution. A script that finds bin/goh or `goh` on PATH on its own is a copy."""
    pattern = re.compile(r'bin/goh"|command -v goh\b')
    offenders = [
        p.name for p in (ROOT / "gates").glob("*.sh")
        if p.name != "_goh_bin.sh" and any(pattern.search(line.split("#", 1)[0]) for line in p.read_text().splitlines())
    ]
    assert offenders == [], f"resolve through gates/_goh_bin.sh: {offenders}"
