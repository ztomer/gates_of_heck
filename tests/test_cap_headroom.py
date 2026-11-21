"""GOH.CAPHEADROOM: a ratchet of source files over 450 lines, the headroom line below the 500 cap.

A file at the cap blocks the next lane that must edit it, and two lanes splitting it two ways
collide at land. Every file over HEADROOM is listed in `tests/cap_headroom_allow.txt`; a new one
fails here before it blocks anyone, and an entry no longer over (or gone) fails as stale, so the
list only shrinks.
"""
# goh-land-guard:

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADROOM = 450
ALLOW = ROOT / "tests" / "cap_headroom_allow.txt"


def _measured_names() -> tuple[list[str], list[str]]:
    src = (ROOT / "crates" / "goh" / "src" / "length.rs").read_text(encoding="utf-8")
    out = []
    for name in ("SOURCE_SUFFIXES", "SOURCE_NAMES"):
        body = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.DOTALL)
        assert body, f"length.rs no longer declares {name}"
        out.append(re.findall(r'"([^"]*)"', body.group(1)))
    return out[0], out[1]


def over_headroom(root: Path = ROOT) -> set[str]:
    suffixes, names = _measured_names()
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.split("\0")
    over = set()
    for rel in filter(None, tracked):
        if not (rel.endswith(tuple(suffixes)) or rel.rsplit("/", 1)[-1] in names):
            continue
        path = root / rel
        if not path.is_file():
            continue
        if len(path.read_bytes().decode("utf-8", "replace").splitlines()) > HEADROOM:
            over.add(rel)
    return over


def allowed() -> set[str]:
    lines = ALLOW.read_text(encoding="utf-8").splitlines()
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


def test_no_new_file_crosses_the_headroom_line() -> None:
    new = sorted(over_headroom() - allowed())
    assert not new, f"over {HEADROOM} lines, split before the next lane needs them: {new}"


def test_stale_entries_fail() -> None:
    stale = sorted(allowed() - over_headroom())
    assert not stale, f"no longer over {HEADROOM} lines; delete from {ALLOW.name}: {stale}"
