"""`check_no_unreaped_spawn.py --probe`: the MEASURED table, run.

Every case below is one row of the table in `check_no_unreaped_spawn.py`'s docstring, which is
itself measured against rustc 1.99.0 on macOS 27.0.1 by a PID-liveness probe (2026-10-03). The
`want_clean` rows matter as much as the rest: a checker that flagged `.output()` or `.status()`
would be flagging the fix, and `.output()` blocks forever on a long-running child -- a real defect
with a different home (a bounded wait in the gate runner), which this scan is not allowed to
pretend to cover.

It is a probe rather than only a unit test because it is what `check_probes_pass.py` runs in every
repo that adopts the gate: a checker whose rule list had been narrowed to nothing would still exit
0 over every real tree, and a gate that scans nothing is indistinguishable from a gate that scans
everything.

THE ROWS THAT MAKE IT A PROBE AND NOT A LIST. Three of them are the shape the gate exists for, and
each is here because deleting its rule must turn this probe red:

  * `reap-after-panic` — the incident, in miniature and in the incident's exact structure.
  * a `kill()` with no `wait()` — measured ZOMBIE, which a reader expects to be fine.
  * a raw `Child` bound with no reap at all — measured LIVE ORPHAN.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_no_unreaped_spawn import REPORTED, verdicts  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok  # noqa: E402


from _unreaped_spawn_table import PYTHON, PYTHON_EXTRA, RUST, SHELL  # noqa: E402
from _unreaped_spawn_table_regressions import (  # noqa: E402
    PYTHON_RAW_QUOTE_CALIBRATION,
    PYTHON_REGRESSIONS,
    RUST_REGRESSIONS,
)


def _got(source: str, ext: str) -> bool:
    """Findings a GATE would fail on, through the same entry point the scan uses.

    `REPORTED` tags (a handoff, a deadline watchdog) are counted and shown, never failed on, so
    they do not count here either -- and the list is imported, so the probe and the gate cannot
    disagree about which tags those are. This goes
    through `verdicts` -- masking included -- because the first version of this probe handed RAW
    source to an analysis function and reported a disagreement the gate itself never had.
    """
    return any(tag not in REPORTED for _, tag, _ in verdicts(source, ext))


def probe() -> int:
    bad = 0
    for label, source, want in RUST:
        got = _got(source, ".rs")
        if got != want:
            err(f"probe [rust]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [rust]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in RUST_REGRESSIONS:
        got = _got(source, ".rs")
        if got != want:
            err(f"probe [rust]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [rust]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in PYTHON_REGRESSIONS:
        got = _got(source, ".py")
        if got != want:
            err(f"probe [python]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [python]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in (PYTHON_RAW_QUOTE_CALIBRATION,):
        got = _got(source, ".py")
        if got != want:
            err(f"probe [python]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [python]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in PYTHON:
        got = _got(source, ".py")
        if got != want:
            err(f"probe [python]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [python]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in PYTHON_EXTRA:
        got = _got(source, ".py")
        if got != want:
            err(f"probe [python]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [python]: {label} -> {'finding' if got else 'clean'}")
    for label, source, want in SHELL:
        got = _got(source, ".sh")
        if got != want:
            err(f"probe [shell]: {label} — wanted findings={want}, got {got}")
            bad += 1
        else:
            ok(f"probe [shell]: {label} -> {'finding' if got else 'clean'}")
    total = (
        len(RUST)
        + len(RUST_REGRESSIONS)
        + len(PYTHON)
        + len(PYTHON_EXTRA)
        + len(PYTHON_REGRESSIONS)
        + 1  # PYTHON_RAW_QUOTE_CALIBRATION, a row that pins a refuted theory
        + len(SHELL)
    )
    if bad:
        err(
            f"check_no_unreaped_spawn --probe: {bad} of {total} measured shapes disagree with the table"
        )
        return 1
    ok(
        f"check_no_unreaped_spawn --probe: {total} shapes, measured against rustc 1.99.0 / CPython "
        "3.14 on 2026-10-03 and 2026-10-05"
    )
    return 0


if __name__ == "__main__":
    sys.exit(probe())
