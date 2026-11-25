"""A measured coverage at the precision its floor is compared at, and the verdict ON that value.

GOH.COVSHOW (AF.GATEFLOORS, 2026-11-12): check_swift_coverage.py compared the full float against
GOH_SWIFT_COV_MIN and printed one decimal, so a floor could only be found by bracketing it -- 89.2
passed and 89.3 failed, and 89.25 was invisible. A floor written to more places than the report
shows cannot be read off the report. So the value is shown to max(2, the floor's own places), cut
(never rounded up, which would show a pass the comparison refuses), and the verdict is taken on
exactly that shown value: writing the shown number as the floor passes, and one unit more in its
last place fails. Pinned by tests/test_cov_shown.py.

A measured coverage at the precision its floor is compared at (GOH.COVSHOW): cut (never rounded up)
to max(2, the floor's own places), and the verdict taken on that shown value, so the number a
passing gate shows, written as the floor, passes, and one unit more in its last place fails. Used
by `check_swift_coverage.py` and `coverage_swift.py`.
"""

from __future__ import annotations

import math

MIN_PLACES = 2


def places(floor: float) -> int:
    """The decimal places a floor is written to (its shortest repr), at least MIN_PLACES."""
    text = repr(
        round(float(floor), 6)
    )  # a bound made by arithmetic (floor - tolerance) has float noise
    if "e" in text or "." not in text:
        return MIN_PLACES
    return max(MIN_PLACES, len(text.split(".", 1)[1].rstrip("0")))


def shown(pct: float, floor: float) -> tuple[str, bool]:
    """(pct as text at the floor's precision, whether that value meets the floor)."""
    p = places(floor)
    scale = 10**p
    # 1e-6 of a unit in the last place absorbs float error just under a boundary (3570/4000 is
    # 89.24999999999999 in binary), as the checkers' old `pct + 1e-9 >= floor` did.
    units = math.floor(pct * scale + 1e-6)
    return f"{units / scale:.{p}f}", units >= round(floor * scale)
