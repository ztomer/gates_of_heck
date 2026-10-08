"""Fake load readers for tests/test_bench_lock.py: what `GOH_BENCH_LOADAVG` points quiet.sh at."""

from __future__ import annotations

from pathlib import Path


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
