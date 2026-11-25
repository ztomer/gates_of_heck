"""Fake load readers for tests/test_bench_lock.py: what `GOH_BENCH_LOADAVG` points quiet.sh at."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from timing_bounds import GiveUp

# lib/bench_lock.sh's 2 s and 0.5 s waits, in every bash a test starts: an exported function.
FAST_POLL = {
    "BASH_FUNC_sleep%%": '() { case "$1" in 2|0.5) set -- 0.1;; esac; command sleep "$@"; }'
}


def says(said: Path, what: str, p: subprocess.Popen[str]) -> None:
    """Until `p`, whose stderr is the file `said`, has said `what`."""
    give_up = GiveUp(60)
    while what not in (said.read_text() if said.exists() else ""):
        assert p.poll() is None, f"it ended without saying {what!r}"
        assert not give_up.passed(), f"it never said {what!r}"
        time.sleep(0.02)


def fake_load(tmp_path: Path, readings: list[int]) -> dict[str, str]:
    """A load reader that plays `readings` (the last repeats) and logs, per read, whether the host
    was held at that moment -- the queue's whole claim is that it holds nothing while it waits."""
    seq = tmp_path / "readings"
    seq.write_text("\n".join(map(str, readings)) + "\n")
    log = tmp_path / "reads"
    script = tmp_path / "load.sh"
    script.write_text(
        "#!/bin/bash\n"
        f'held=free; [ -d "{tmp_path}/lock/exclusive" ] && held=held\n'
        f'v="$(head -n 1 "{seq}")"; [ "$(wc -l < "{seq}")" -gt 1 ] && tail -n +2 "{seq}" > "{seq}.n" '
        f'&& mv "{seq}.n" "{seq}"\n'
        f'echo "$held $v" >> "{log}"; echo "$v"\n'
    )
    script.chmod(0o755)
    return {"GOH_BENCH_LOADAVG": str(script)}


def busy_for(tmp_path: Path, seconds: int) -> dict[str, str]:
    """A load of 50 for `seconds` from its first read, then 1. By the clock, not by a count of
    reads: quiet.sh reads once per attempt, and an attempt costs spawns, so a count meant as ~15 s
    stretched past 40 s on a loaded box and timed the test out (a land gate, 2026-10-08)."""
    start = tmp_path / "busy_since"
    script = tmp_path / "load.sh"
    script.write_text(
        "#!/bin/bash\n"
        f'[ -f "{start}" ] || date +%s > "{start}"\n'
        f'if [ $(( $(date +%s) - $(cat "{start}") )) -lt {seconds} ]; then echo 50; else echo 1; fi\n'
    )
    script.chmod(0o755)
    return {"GOH_BENCH_LOADAVG": str(script)}


def gate_load(tmp_path: Path) -> dict[str, str]:
    """A load that is high exactly while a gate RUNS: the box the other sessions make. (Not while
    one is registered: a join lost to a claim between its look and its mark is registered for
    an instant.)"""
    script = tmp_path / "gate_load.sh"
    script.write_text(
        "#!/bin/bash\n"
        f'held=free; [ -d "{tmp_path}/lock/exclusive" ] && held=held\n'
        f'v=1; [ -n "$(ls -A "{tmp_path}/running" 2>/dev/null)" ] && v=50\n'
        f'echo "$held $v" >> "{tmp_path}/reads"; echo "$v"\n'
    )
    script.chmod(0o755)
    return {"GOH_BENCH_LOADAVG": str(script)}


def controls(tmp_path: Path, *seconds: str) -> dict[str, str]:
    seq = tmp_path / "controls"
    seq.write_text("\n".join(seconds) + "\n")
    script = tmp_path / "control.sh"
    script.write_text(
        f'#!/bin/bash\nhead -n 1 "{seq}"; tail -n +2 "{seq}" > "{seq}.n"; mv "{seq}.n" "{seq}"\n'
    )
    script.chmod(0o755)
    return {"GOH_BENCH_CONTROL": str(script)}
