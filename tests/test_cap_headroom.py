"""GOH.CAPHEADROOM: a ratchet of source files over 450 lines, the headroom line below the 500 cap.

A file at the cap blocks the next lane that must edit it, and two lanes splitting it two ways
collide at land. Every file over HEADROOM is listed in `tests/cap_headroom_allow.txt`; a new one
fails here before it blocks anyone, and an entry no longer over (or gone) fails as stale, so the
list only shrinks.
"""
# goh-land-guard:

from pathlib import Path

from lib import tables

ROOT = Path(__file__).resolve().parent.parent
HEADROOM = tables.HEADROOM
ALLOW = ROOT / "tests" / "cap_headroom_allow.txt"


def over_headroom(root: Path = ROOT) -> set[str]:
    return tables.over_headroom(root)


def allowed() -> set[str]:
    lines = ALLOW.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def test_no_new_file_crosses_the_headroom_line() -> None:
    new = sorted(over_headroom() - allowed())
    assert not new, f"over {HEADROOM} lines, split before the next lane needs them: {new}"


def test_stale_entries_fail() -> None:
    stale = sorted(allowed() - over_headroom())
    assert not stale, f"no longer over {HEADROOM} lines; run `{tables.FIX}`: {stale}"
