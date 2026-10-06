"""One fact, one git spawn: a structural run asks git each question once (2026-10-06).

A `goh structural --staged` over a one-file repo spawned git 16 times, six of them the same
`rev-parse --show-toplevel` -- each step asked for itself. Spawns are this box's cost metric, not
CPU (docs/BACKLOG.md), and sys-heavy work is what serializes across concurrent sessions
(tools/session_bench.py: one structural run is 9 s sys for 4 s user). A git shim first on PATH
logs every call; the counts here are a ratchet -- lower them, never raise them.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path

import pytest
from conftest import hermetic_env

REAL_GIT = subprocess.run(
    ["sh", "-c", "command -v git"], capture_output=True, text=True
).stdout.strip()


def _calls(goh: Path, tmp_path: Path, *args: str) -> Counter:
    shim, log = tmp_path / "shim", tmp_path / "git.log"
    shim.mkdir()
    # Each line names the CALLER: the binary's own questions are the ratchet's subject; a delegated
    # Python checker is its own process and asks for itself.
    (shim / "git").write_text(
        f'#!/bin/sh\necho "$(basename "$(ps -o comm= -p $PPID)") :: $*" >> "{log}"\n'
        f'exec "{REAL_GIT}" "$@"\n'
    )
    (shim / "git").chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("hi\n")
    for cmd in (["init", "-q"], ["add", "-A"]):
        subprocess.run([REAL_GIT, "-C", str(repo), *cmd], check=True)
    env = hermetic_env(drop_git=True)
    env["PATH"] = f"{shim}:{env['PATH']}"
    r = subprocess.run(
        [str(goh), "structural", *args], cwd=repo, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return Counter(
        args
        for caller, _, args in (line.partition(" :: ") for line in log.read_text().splitlines())
        if caller == goh.name
    )


@pytest.mark.parametrize("mode", ["--staged", "--full"])
def test_the_binary_asks_for_the_top_level_once(goh: Path, tmp_path: Path, mode: str) -> None:
    calls = _calls(goh, tmp_path, mode)
    assert calls["rev-parse --show-toplevel"] <= 1, calls


def test_a_staged_run_of_the_binary_spawns_git_at_most_this_often(
    goh: Path, tmp_path: Path
) -> None:
    calls = _calls(goh, tmp_path, "--staged")
    assert sum(calls.values()) <= 9, calls  # 16 before 2026-10-06
