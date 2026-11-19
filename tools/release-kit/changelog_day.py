"""changelog_day.py -- the day a changelog cut is stamped: `GOH_TODAY` (YYYY-MM-DD), else today.

The clock seam (GOH.MIDNIGHTDATE): a gate that crossed midnight split the day four tests expected
(read from the clock) from the day `changelog_cut.py` wrote (read later), and a correct tree went
red. The suite pins `GOH_TODAY` for every hermetic child (`tests/suite_home.py` `CLOCK`). A
malformed value raises: a wrong pin is a finding, never a silent fall back to the wall clock.
"""

from __future__ import annotations

import datetime
import os


def today() -> str:
    pinned = os.environ.get("GOH_TODAY")
    day = datetime.date.fromisoformat(pinned) if pinned else datetime.date.today()
    return day.isoformat()
