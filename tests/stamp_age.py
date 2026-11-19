"""Judge a stamp the code under test WROTE by its age, never against a date read from the clock.

A test that expects `since <today>` reads the clock apart from the code that stamped it, and a gate
crossing midnight between the two reds a correct tree (GOH.MIDNIGHTDATE; the guard is
tests/test_no_wallclock_expected.py). The stamp is read off the line and its AGE checked instead.
"""

from __future__ import annotations

import re
import time

STAMP_AT = r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)"
STAMP = re.compile(f"since {STAMP_AT}")


def assert_stamped_just_now(line: str) -> None:
    """The holder stamped `since <local date time>` when it took the slot: the stamp is READ off
    the line and judged by its AGE, never compared with a date the test reads from the clock
    itself, which a gate crossing midnight between the two splits (GOH.MIDNIGHTDATE)."""
    m = STAMP.search(line)
    assert m, f"no `since YYYY-MM-DD HH:MM:SS` stamp in {line!r}"
    age = time.time() - time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    assert -5 <= age <= 600, f"the holder's stamp is {age:.0f}s old, not just taken: {line!r}"
