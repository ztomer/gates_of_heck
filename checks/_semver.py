"""Semver, narrowly: what a Cargo.toml actually spells.

Split out of `check_dep_currency.py` for the file-length cap, and because
these rules are the part that can be wrong SILENTLY -- a comparator that
misreads `0.19` invents findings in every repo, and a comparator that
misreads a prerelease picks the wrong "newest" version with no symptom.

Two rules that are easy to get backwards, and were:

* **`0.x` is not "same major".** Cargo's caret makes `0.19` mean
  `>=0.19.0, <0.20.0`, so `fancy-regex 0.14 -> 0.19` crosses five 0.x
  minors and every one of them breaks the API.
* **a bare three-part requirement is a CARET requirement, not an exact
  one.** `1.0.5` admits `1.0.6`. Two of the probe's own expectations said
  otherwise before the code was checked against them.

An unreadable requirement returns `None`, never a verdict: a check that
guesses `">=1.2, <2"` means invents findings nobody can act on.
"""

from __future__ import annotations

import re

# A comparator we can interpret. Anchored at BOTH ends on purpose: trailing
# junk is not a version, and reading `^1.2.3.4.5` as `^1.2.3` is a guess.
_COMPARATOR = re.compile(r"^(>=|<=|>|<|=|~|\^)?\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

# A version as a key that ORDERS CORRECTLY UNDER `<` and `max()`.
#
# The prerelease field is `(1, "")` for a RELEASE and `(0, tag)` for a
# prerelease, so a release sorts above every prerelease of the same triple by
# plain tuple comparison. The obvious encoding -- `""` for a release -- is
# backwards, and a test caught it rather than reading: the caller does
# `max(held, key=parse_version)`, which reported `1.0.0-rc.1` as newer than
# `1.0.0`.
VersionKey = tuple[int, int, int, tuple[int, str]]


#
# Only what a Cargo.toml actually spells. Build metadata (`1.1.6+spec-1.1.0`)
# is ignored, as semver requires. A prerelease compares BELOW its release.


# A version as a key that ORDERS CORRECTLY UNDER `<` and `max()`.
#
# The prerelease field is `(1, "")` for a RELEASE and `(0, tag)` for a
# prerelease, so a release sorts above every prerelease of the same triple by
# plain tuple comparison. The obvious encoding -- `""` for a release -- is
# backwards, and it was caught by a test rather than by reading: this module
# calls `max(held, key=parse_version)`, which would have picked `1.0.0-rc.1`
# over `1.0.0` and reported the wrong "newest version in the graph".
VersionKey = tuple[int, int, int, tuple[int, str]]


def parse_version(text: str) -> VersionKey | None:
    t = text.strip().split("+", 1)[0]
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$", t)
    if m:
        pre = (0, m.group(4)) if m.group(4) else (1, "")
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)
    m = re.match(r"^(\d+)\.(\d+)$", t)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0, (1, ""))
    m = re.match(r"^(\d+)$", t)
    if m:
        return (int(m.group(1)), 0, 0, (1, ""))
    return None


def _cmp(a: VersionKey, b: VersionKey) -> int:
    return -1 if a < b else (0 if a == b else 1)


def _opt(text: str | None) -> int | None:
    return int(text) if text is not None else None


def _key(major: int, minor: int = 0, patch: int = 0) -> VersionKey:
    return (major, minor, patch, (1, ""))


def _bounds(op: str, maj: int, mn: int | None, pat: int | None):
    """`(lowest admitted, lowest refused above)` for one comparator, either side None for open.

    Cargo's table, PARTIAL versions included (doc.rust-lang.org, "Specifying dependencies"): a
    missing minor or patch widens the range rather than meaning zero. Reading `~1.0` as `~1`,
    `=1.2` as `=1.2.0` and `^0.0.3` as `^0.0` were three readings of one mistake -- a missing
    field taken as 0 -- found porting this comparator (Phase N1).
    """
    m0, p0 = mn or 0, pat or 0
    if op == "=":
        if mn is None:
            return _key(maj), _key(maj + 1)
        if pat is None:
            return _key(maj, mn), _key(maj, mn + 1)
        return _key(maj, mn, pat), _key(maj, mn, pat + 1)
    if op == ">=":
        return _key(maj, m0, p0), None
    if op == ">":
        if mn is None:
            return _key(maj + 1), None
        if pat is None:
            return _key(maj, mn + 1), None
        return _key(maj, mn, pat + 1), None
    if op == "<":
        return None, _key(maj, m0, p0)
    if op == "<=":
        if mn is None:
            return None, _key(maj + 1)
        if pat is None:
            return None, _key(maj, mn + 1)
        return None, _key(maj, mn, pat + 1)
    if op == "~":
        if mn is None:
            return _key(maj), _key(maj + 1)
        return _key(maj, mn, p0), _key(maj, mn + 1)
    # `^`, and a bare requirement, which is a caret one.
    if maj > 0 or mn is None:
        return _key(maj, m0, p0), _key(maj + 1)
    if mn > 0 or pat is None:
        return _key(0, mn, p0), _key(0, mn + 1)
    return _key(0, 0, pat), _key(0, 0, pat + 1)


def req_allows(req: str, version: str) -> bool | None:
    """Does `req` admit `version`? `None` when the requirement is unreadable.

    Cargo's default is a CARET requirement, so `1` means `>=1.0.0, <2.0.0` and
    `0.19` means `>=0.19.0, <0.20.0` — the 0.x rule is the one that makes a
    naive "same major" comparison wrong, and `fancy-regex 0.14 -> 0.19` is
    precisely a case where it matters.
    """
    v = parse_version(version)
    if v is None:
        return None
    r = req.strip()
    if r in ("*", ""):
        return True
    for part in (p.strip() for p in r.split(",")):
        if not part:
            continue
        m = _COMPARATOR.match(part)
        if not m:
            return None
        op, maj_s, min_s, pat_s = m.groups()
        lo, hi = _bounds(op or "^", int(maj_s), _opt(min_s), _opt(pat_s))
        if lo is not None and _cmp(v, lo) < 0:
            return False
        if hi is not None and _cmp(v, hi) >= 0:
            return False
    return True


def major_of(version: str) -> int | None:
    v = parse_version(version)
    return v[0] if v else None
