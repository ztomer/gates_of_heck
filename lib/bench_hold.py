"""Hold the host's bench lock for a Python block: `with bench_hold.held(label, hold_s=...):`.

`tools/quiet.sh` holds the host around ONE command, and its hold is capped (GOH_BENCH_MAX_HOLD,
900 s) because a hold blocks every session's commits. A measurement longer than the cap -- a
forty-minute census -- cannot sit in one hold, but each of its pieces can: claim, read, let go,
and the other sessions' gates run between the pieces and never inside one (ZoneWM's census set 16
time cells aside on 2026-10-11 under another session's gates, holding nothing).

The lock itself stays `lib/bench_lock.sh`, the one implementation every gate joins: this module
runs it in a bash child that claims the host, prints `held`, and keeps the claim until its stdin
closes. A pipe closes when its writer dies, so a caller that is SIGKILLed releases too -- the
same guarantee the lock's own dead-owner rule gives, a step sooner.

    with bench_hold.held("census: window drag", hold_s=600, claim_s=120, drain_s=120):
        measure()

Raises `BenchBusy` (naming the holder or the gates still running) before the block runs when the
host cannot be had within `claim_s` + `drain_s`; the caller decides whether to run unheld. A hold
whose claim went void under it (it outlived its stamp, and a waiter reclaimed the host) is
reported by `Hold.void` after the block, so a reading taken past its hold is not passed as quiet.

Pinned by tests/test_bench_hold.py.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

LIB = Path(__file__).resolve().parent / "bench_lock.sh"
VOID = 3  # the holder's exit status when the claim was no longer its own at release
HELD = "held"

# Claim (bounded by GOH_BENCH_WAIT), drain (bounded by $3), stamp the hold, say so, wait for EOF.
# At release, a claim that is no longer ours went void under the block: say so and exit VOID.
_HOLDER = f"""
. "$1" || exit 2
trap 'bench_lock_release' EXIT
bench_lock_exclusive "$2" "$3" || exit 1
bench_lock_hold "$4" || {{ printf 'bench hold: a %ss hold is past the cap\\n' "$4" >&2; exit 1; }}
echo {HELD}
read -r _ || :
[ "$(_bench_owner_field 1)" = "$$" ] || {{ BENCH_LOCK_HELD=0; exit {VOID}; }}
"""


class BenchBusy(RuntimeError):
    """The host could not be held; the message is the lock's own reason."""


@dataclass
class Hold:
    """What the block ran under: `void` is set after the block when the claim went void in it."""

    label: str
    void: bool = False


@contextlib.contextmanager
def held(label: str, *, hold_s: int, claim_s: int = 120, drain_s: int = 120, lib: Path = LIB):
    """Hold the host for the block, or raise BenchBusy before it runs."""
    env = {**os.environ, "GOH_BENCH_WAIT": str(claim_s + drain_s)}
    proc = subprocess.Popen(
        ["bash", "-c", _HOLDER, "bench_hold", str(lib), label, str(drain_s), str(hold_s)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )  # fmt: skip
    assert proc.stdin and proc.stdout and proc.stderr
    if proc.stdout.readline().strip() != HELD:
        proc.stdin.close()
        why = proc.stderr.read().strip()
        proc.wait()
        raise BenchBusy(why or f"the bench holder exited {proc.returncode} without holding")
    hold = Hold(label)
    try:
        yield hold
    finally:
        proc.stdin.close()
        hold.void = proc.wait() == VOID
        proc.stdout.close()
        proc.stderr.close()
