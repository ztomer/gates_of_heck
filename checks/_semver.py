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
        maj, mn = int(maj_s), int(min_s or 0)
        pat = int(pat_s) if pat_s is not None else None
        base = (maj, mn, pat or 0, (1, ""))

        if op in (">=",):
            if _cmp(v, base) < 0:
                return False
        elif op == ">":
            if _cmp(v, base) <= 0:
                return False
        elif op == "<=":
            if _cmp(v, base) > 0:
                return False
        elif op == "<":
            if _cmp(v, base) >= 0:
                return False
        elif op in ("=", "^", "~", None):
            if op == "^" or op is None:
                # caret: leftmost non-zero component is the floor
                if maj > 0:
                    hi = (maj + 1, 0, 0, (1, ""))
                elif mn > 0 or pat is not None:
                    hi = (0, mn + 1, 0, (1, ""))
                else:
                    hi = (1, 0, 0, (1, ""))
                if _cmp(v, base) < 0 or _cmp(v, hi) >= 0:
                    return False
            elif op == "~":
                # `~1.2.3` is >=1.2.3 <1.3.0 and `~1.2` is >=1.2.0 <1.3.0:
                # the tilde pins the MINOR when a minor is named, and the
                # MAJOR when only a major is. Pinning the patch here refused
                # 1.2.9 to `~1.2.3`, which is not what tilde means.
                hi = (
                    (maj + 1, 0, 0, (1, ""))
                    if (pat is None and mn == 0)
                    else (maj, mn + 1, 0, (1, ""))
                )
                if _cmp(v, base) < 0 or _cmp(v, hi) >= 0:
                    return False
            else:  # exact
                if v[:3] != base[:3]:
                    return False
        else:  # pragma: no cover - unreachable, the regex admits nothing else
            return None
    return True


def major_of(version: str) -> int | None:
    v = parse_version(version)
    return v[0] if v else None
