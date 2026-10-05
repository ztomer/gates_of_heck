"""WHERE a spawned child's REAP is, and what counts as stopping it.

    checks/_spawn_reap.py            # nothing to run; import it

Split out of `_spawn_rust.py` at the 500-line cap. The cut is at a real boundary: this file answers
one question -- given a spawn, which line below it ends the child's life -- and the answer is
independent of where the spawn is or what protects it.

# STOP AND COLLECT ARE DIFFERENT, AND ONLY ONE OF THEM IS THIS GATE'S BUSINESS

`kill` STOPS the child; `wait` only COLLECTS the corpse. That is not a distinction this gate invented,
it is the measured table: `kill()` with no `wait()` leaves a **ZOMBIE**, which cannot hold a build
lock, cannot outlive the run and cannot make a suite hang -- it is collected when the parent exits.
Only the first ends a leak.

So `kill()` alone is deliberately NOT a finding, and it is the row a reader is most likely to argue
with. The estate sweep is what forced it: four of the first seven hits across twelve repos were
CORRECT code, and a checker that flags the fix is a checker that gets switched off. `wait()` alone
DOES end the leak, because it blocks until the child exits.

# THE ORDER OF THE TWO IS ALSO MEASURED, NOT ASSUMED

`kill()` before the first `assert!`, `wait()` after it, is clean -- the child is already stopped when
anything can panic. `wait()` before the first `assert!` and `kill()` after it is not. So this returns
the FIRST line that stops the child, preferring `kill`, and the caller compares it against the
earliest panicking construct between the spawn and that line.
"""

import re

# `kill` ends the leak. `start_kill`/`terminate`/`send_signal` are the platform spellings of the same
# act and were already in this list.
STOP = re.compile(r"\.\s*(?:kill|start_kill|terminate|send_signal)\s*\(")
# `wait` collects the corpse and, on its own, also ends the leak by BLOCKING until the child exits.
COLLECT = re.compile(r"\.\s*(?:wait|wait_with_output|wait_timeout|try_wait)\s*\(")


def reap_line(lines, start, close):
    """The line that STOPS the child (`kill`), or the `wait` when nothing kills it.

    Bounded by the enclosing function, so a `wait` in the NEXT test cannot reap this one -- the same
    scope discipline as the guard rules, and the same bug it was fixing.
    """
    first_stop = first_collect = None
    for j in range(start, min(close + 1, len(lines))):
        if first_stop is None and STOP.search(lines[j]):
            first_stop = j
        if first_collect is None and COLLECT.search(lines[j]):
            first_collect = j
        if first_stop is not None and first_collect is not None:
            break
    return first_stop if first_stop is not None else first_collect
