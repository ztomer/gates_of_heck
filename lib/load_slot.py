"""The load slot when it is taken: wait for it, or refuse naming the holder and the line to paste.

GOH.LOADREFUSE (2026-11-11, docs/decisions/D-0002): a refusal that said only "another load
experiment already holds the slot" was stepped around twice in one day by running the gate bare,
which loads the machine just the same and is tracked by nothing. So the right path is the easy one:

* `--wait` (bounded by `--wait-max`) waits for the slot as well as for the gates in flight;
* a run with NO spinners (`load.py 0 -- <gate>`) waits for the slot by default: it measures
  nothing, so waiting costs no experiment its meaning, and refusing it only sends it round;
* a refusal names the holder (pid, start, spinners, command) and prints the exact rerun line,
  `--wait` added, for an experiment with spinners that did not ask to wait.
"""

from __future__ import annotations

import shlex
import time
from collections.abc import Callable, Sequence
from pathlib import Path

POLL_S = 0.25  # how often a waiting run asks for the slot again
TOOL = Path(__file__).resolve().parent.parent / "tools" / "load.py"


def waits(count: int, asked: bool) -> bool:
    """Whether a run waits for a taken slot: asked to, or it starts no spinner (D-0002)."""
    return asked or count == 0


def rerun_line(argv: Sequence[str]) -> str:
    """The command to paste: this tool, `--wait`, then the arguments it was given (an existing
    `--wait` dropped, so it is said once)."""
    rest = [a for a in argv if a != "--wait"]
    return f"python3 {shlex.quote(str(TOOL))} --wait {shlex.join(rest)}"


def claim(
    take: Callable[[], None], wait: bool, wait_s: float, say: Callable[[str], None]
) -> tuple[Path | None, float]:
    """`take()` the slot; while it raises FileExistsError and `wait` holds, ask again until
    `wait_s` has passed. Returns (the holder's registry when refused, seconds waited)."""
    start = time.monotonic()
    poll = POLL_S
    told = False
    while True:
        try:
            take()
            return None, time.monotonic() - start
        except FileExistsError as taken:
            waited = time.monotonic() - start
            if not wait or waited >= wait_s:
                return Path(str(taken)), waited
            if not told:
                say(f"load: waiting for the slot (at most {wait_s:g}s): {taken}")
                told = True
            time.sleep(min(poll, max(0.0, wait_s - waited)))


def refusal(holder: Path, described: str, argv: Sequence[str], waited: bool, wait_s: float) -> str:
    """Why the slot is not ours, who has it, and the one line that gets on."""
    if waited:
        head = f"load: --wait-max of {wait_s:g}s expired; the slot is still held: {holder}"
        way = "Wait longer: the same line with a larger --wait-max."
    else:
        head = f"load: another load experiment already holds the slot: {holder}"
        way = f"Rerun to wait for it (bounded by --wait-max):\n        {rerun_line(argv)}"
    return "\n      ".join([
        head,
        f"held by {described}",
        (
            "One experiment at a time: a second load measures a machine twice as busy, and "
            "neither can be told from the other afterwards."
        ),
        way,
        (
            "If that experiment is gone: tools/load.py --reap (or wait for the backstop, which "
            "reads the same registry every minute)."
        ),
    ])  # fmt: skip


__all__ = ["POLL_S", "claim", "refusal", "rerun_line", "waits"]
