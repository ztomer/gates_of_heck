"""A test never builds an EXPECTED calendar date from the wall clock (GOH.MIDNIGHTDATE).

THE CLASS (2026-11-19). A land gate crossed midnight and four changelog tests went red on a
correct tree: each expected `_(2026-11-18)_`, the cutter wrote `_(2026-11-19)_`, because the test
(`datetime.date.today()` at import) and the code under test (`date.today()` at run) each read the
clock, minutes apart. The same shape sat in the gate-slot tests (`time.strftime("%Y-%m-%d")`
against a stamp the holder wrote earlier). Any expected value that is a calendar date and comes
from the clock is a test that fails once a day at midnight, and only on the one gate that happens
to straddle it.

The way out is a seam, never a retry: the code takes its day from `GOH_TODAY` (changelog_cut.py),
the suite pins `suite_home.PINNED_TODAY` for every hermetic child, and a stamp the code wrote is
READ back and judged by its age (`stamp_age.assert_stamped_just_now`).

This check reads every `tests/*.py` and refuses a wall-clock CALENDAR read: `date.today()`,
`datetime.today()`, `time.strftime(fmt)` with no time argument, and `datetime.now()`/`utcnow()`
turned into a date (`.date()`, `.strftime()`, `.year`/`.month`/`.day`). A relative read -- `now -
timedelta(days=5)`, `time.time() - 3600` -- is a distance, not a date, and is not refused.
Where a clock date is legitimate (the test IS about the clock), the call goes in ALLOW below
with its reason; an entry that no longer matches a call is stale and fails, so the list cannot
outlive the code it excuses.
"""

from __future__ import annotations

import ast
import re
from collections import Counter

from conftest import REPO_ROOT

TESTS = REPO_ROOT / "tests"

# (file name, source of the call) -> why a clock date is right there; every other test pins.
ALLOW: dict[tuple[str, str], str] = {
    ("test_changelog_cut.py", "datetime.date.today()"): (
        "the unset-GOH_TODAY test IS about the wall clock; it brackets the cut between two reads "
        "and accepts either day, so a midnight crossing inside it cannot split the expectation"
    ),
}

_NOW = {"now", "utcnow"}
_WORD = re.compile(r"\.\s*(?:today|strftime|now|utcnow)\b")
_DATEISH = {"date", "strftime", "year", "month", "day"}


def _is_clock_date(node: ast.AST, parent: ast.AST | None) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    attr = node.func.attr
    owner = node.func.value
    owner_name = owner.id if isinstance(owner, ast.Name) else getattr(owner, "attr", "")
    if attr == "today" and owner_name in {"date", "datetime"}:
        return True
    if attr == "strftime" and owner_name == "time" and len(node.args) == 1:
        return True  # time.strftime(fmt) formats NOW; one with a time tuple formats that instant
    if attr in _NOW:  # a now(): a date only once it is read as one, never `now - delta`
        return isinstance(parent, ast.Attribute) and parent.attr in _DATEISH
    return False


def clock_dates(source: str) -> list[str]:
    """Each wall-clock calendar read in `source`, as the call's own source text."""
    if not _WORD.search(source):
        return []  # no such call: not parsed (1.8 s over the suite)
    tree = ast.parse(source)
    parents = {child: p for p in ast.walk(tree) for child in ast.iter_child_nodes(p)}
    return [ast.unparse(n) for n in ast.walk(tree) if _is_clock_date(n, parents.get(n))]


def _found() -> Counter[tuple[str, str]]:
    out: Counter[tuple[str, str]] = Counter()
    for path in sorted(TESTS.glob("*.py")):
        for call in clock_dates(path.read_text(encoding="utf-8")):
            out[(path.name, call)] += 1
    return out


def test_no_test_builds_an_expected_date_from_the_wall_clock():
    found = _found()
    bad = sorted(k for k in found if k not in ALLOW)
    assert not bad, (
        "a test reads the wall-clock DATE, so it goes red when a gate crosses midnight; pin it "
        "(GOH_TODAY / suite_home.PINNED_TODAY) or judge a stamp by its age, or add the call to "
        f"ALLOW with its reason (GOH.MIDNIGHTDATE): {bad}"
    )
    stale = sorted(k for k in ALLOW if k not in found)
    assert not stale, f"ALLOW names a clock read no test makes any more; delete it: {stale}"
    assert all(ALLOW.values()), "an ALLOW entry needs its reason"


# --- the instrument can be wrong: each shape the incident had, and the ones it must not refuse ---


def test_the_check_sees_the_shapes_that_went_red_at_midnight():
    cases = {
        "import datetime\nT = datetime.date.today().isoformat()\n": "datetime.date.today()",
        "from datetime import date\nT = date.today()\n": "date.today()",
        "import time\ntoday = time.strftime('%Y-%m-%d')\n": "time.strftime('%Y-%m-%d')",
        "import datetime as dt\nd = dt.datetime.now(dt.UTC).date()\n": "dt.datetime.now(dt.UTC)",
        "from datetime import datetime\nd = datetime.now().strftime('%F')\n": "datetime.now()",
        "from datetime import datetime\nd = datetime.utcnow().year\n": "datetime.utcnow()",
    }
    for src, call in cases.items():
        assert clock_dates(src) == [call], (src, clock_dates(src))


def test_the_check_leaves_a_distance_from_now_alone():
    for src in (
        "import datetime as dt\nold = dt.datetime.now(dt.UTC) - dt.timedelta(days=5)\n",
        "import time\nthen = time.time() - 3600\n",
        "import time\nstamp = time.strftime('%H', time.localtime(then))\n",
        (
            "import datetime as dt\nNOW = dt.datetime.now(dt.UTC)\n"
            "x = (NOW - dt.timedelta(1)).strftime('%F')\n"
        ),
        "import datetime as dt\nfirst = dt.datetime.now(dt.UTC).isoformat()\n",
    ):
        assert clock_dates(src) == [], (src, clock_dates(src))
