"""A release push's near-line counts warn instead of failing (owner 2026-11-26: "Go ahead").

Six release pushes of one ship went red with every test passing (9517 passed): each red was a
count at its line (154 file events against 150, 2 tree walks against 0, 48 procs against 24) or a
named limit gone stale, and each retry's whole run made more of them. A count's line is judged on
every land; a release push is the one place a run is whole, so there a count within FACTOR of its
limit (or within ZERO_SLACK of a zero limit) and a stale name warn, and anything else fails as
before: a blowup, a whole run's total (GOH.SUITETOTAL), a slow file, a line this module does not
read. Second read: Neon (GOH.TESTWORKHOST), scoped from "everything advisory" to this.

tools/release_goh.sh sets VAR around its pushes. It is read once, at import, and removed from the
environment, so no test's child pytest inherits it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable

VAR = "GATES_OF_HECK_COUNTS_ADVISORY"  # not GOH_: a hermetic child that drops GOH_* keeps it
AT_RELEASE = os.environ.pop(VAR, "") == "release"
FACTOR = 2.5  # 303 events against 150 is at the line; 376 is not
ZERO_SLACK = 2  # a zero limit: 2 tree walks is at the line, 3 is not

_WORK = re.compile(r" \w+ ([\d.]+), over ([\d.]+) \(")  # lib/test_work.py faults
_EVENTS = re.compile(r" made (\d+) \w+, over .*'s ceiling (\d+)$")  # tests/_fs_ceilings.py
_STALE = (" is named for ", ": remove its name")  # test_work.stale, _fs_ceilings.faults


def near_line(got: float, limit: float) -> bool:
    return got <= (limit * FACTOR if limit > 0 else ZERO_SLACK)


def advisory(line: str) -> bool:
    if any(mark in line for mark in _STALE):
        return True
    m = _WORK.search(line) or _EVENTS.search(line)
    return bool(m) and near_line(float(m.group(1)), float(m.group(2)))


def split(lines: Iterable[str], release: bool | None = None) -> tuple[list[str], list[str]]:
    """(the lines that fail, the lines that warn): at a release push the near-line ones warn."""
    lines = list(lines)
    if not (AT_RELEASE if release is None else release):
        return lines, []
    return [ln for ln in lines if not advisory(ln)], [ln for ln in lines if advisory(ln)]
