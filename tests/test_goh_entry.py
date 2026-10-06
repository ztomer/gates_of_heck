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
    return subprocess.run(
        ["bash", str(ENTRY), *args], capture_output=True, text=True, cwd=ROOT, env={**base, **env}
    )


def fake_native(tmp_path: Path) -> Path:
    fake = tmp_path / "goh"
    fake.write_text('#!/bin/sh\necho "native $*"\n')
    fake.chmod(0o755)
    return fake


def test_a_resolved_binary_runs_with_the_arguments_as_given(tmp_path: Path) -> None:
    r = run("home-paths", "--staged", "--exclude", "^x/", GOH_BIN=str(fake_native(tmp_path)))
    assert r.returncode == 0
    assert r.stdout.strip() == "native home-paths --staged --exclude ^x/"


def test_a_named_binary_that_is_not_executable_is_reported_and_refused(tmp_path: Path) -> None:
    """Phase N3: the binary is the only tier, so a missing one is a refusal that names it --
    never the Python reference run in its place, which would be a different gate, unannounced."""
    r = run("markers", "--staged", GOH_BIN=str(tmp_path / "missing"))
    assert r.returncode == 2, r.stdout + r.stderr  # cannot judge, never a finding (1) or a pass
    assert r.stderr.count("is not an executable") == 1, r.stderr
    assert "build-goh.sh" in r.stderr, "the refusal does not say how to fix it"
    assert "no_conflict_markers" not in r.stdout, "the retired Python checker ran"


def test_no_native_is_retired_said_and_not_obeyed(tmp_path: Path) -> None:
    """`GOH_NO_NATIVE` selected the Python tier, which is gone. A CI step still setting it
    (monitor's did) keeps working on the one tier there is, and is told the key is inert."""
    r = run("markers", "--staged", GOH_NO_NATIVE="1", GOH_BIN=str(fake_native(tmp_path)))
    assert r.stdout.strip() == "native markers --staged"
    assert "GOH_NO_NATIVE is retired" in r.stderr


def test_an_argument_only_python_takes_runs_python(tmp_path: Path) -> None:
    r = run("unreaped-spawn", "--probe", GOH_BIN=str(fake_native(tmp_path)))
    assert "native" not in r.stdout
    assert "Python-only" in r.stderr


def test_an_unknown_check_is_refused() -> None:
    assert run("frobnicate").returncode == 2


def test_no_gate_script_resolves_the_binary_itself() -> None:
    """THE GATE: one resolution. A script that finds bin/goh or `goh` on PATH on its own is a copy."""
    pattern = re.compile(r'bin/goh"|command -v goh\b')
    offenders = [
        p.name
        for p in (ROOT / "gates").glob("*.sh")
        if p.name != "_goh_bin.sh"
        and any(pattern.search(line.split("#", 1)[0]) for line in p.read_text().splitlines())
    ]
    assert offenders == [], f"resolve through gates/_goh_bin.sh: {offenders}"


def test_every_dispatched_check_names_a_real_reference_and_a_real_subcommand(goh: Path) -> None:
    """A row in goh.sh's table is two promises: a native subcommand `goh <check>` exists, and the
    reference file a fallback runs exists. Each Phase N1 port adds a row, and a row naming a
    subcommand the binary lacks makes goh.sh exec into "unrecognized subcommand" -- a check that
    looks available and is not."""
    text = ENTRY.read_text(encoding="utf-8")
    table = text[text.index('case "$check" in') : text.index("esac")]
    rows = re.findall(r'^\s*([\w-]+)\)\s+python_file="([^"]+)"', table, re.M)
    assert len(rows) >= 18, rows
    python_only: set[str] = set()
    for check, ref in rows:
        assert (ROOT / ref).is_file(), (check, ref)
        if check in python_only:
            continue
        r = subprocess.run([str(goh), check, "--help"], capture_output=True, text=True)
        assert r.returncode == 0, (check, r.stderr)


def test_a_bash_reference_falls_back_to_bash(tmp_path: Path) -> None:
    """`shell-lint`'s reference is `check_shell_lint.sh`; the fallback ran it under python3."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    env = {k: v for k, v in os.environ.items() if k != "GOH_BIN"}
    r = subprocess.run(
        ["bash", str(ENTRY), "shell-lint"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**env, "GOH_NO_NATIVE": "1"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[shell_lint]" in r.stdout + r.stderr, r.stdout + r.stderr
