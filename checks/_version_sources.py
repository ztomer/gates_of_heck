"""Version-source TEXT -> the release numbers it declares. Pure: no I/O, no git, no repo.

Split out of `check_tag_version.py` for the reason `_cargo_toml.py` is: the strategies are the part
worth testing on their own and the part most likely to be re-implemented. "Where does this repo say
what version it is" is a question every release gate asks, and each answer that lives next to its
own gate is one more answer to drift.

A STRATEGY is `(text) -> [(table, version)]` and the registry below is keyed by the `kind` a repo
names in `GOH_TAG_VERSION_SOURCES`. Adding a layout is one entry here.

SELECTION IS BY VALUE SHAPE, never by position, and that is the rule each strategy here follows.
These files carry more than one plausible-looking declaration — a marketing version AND a build
number, a workspace table AND a member table — so "the first match" is a coin toss that reads as
truth. Every declaration a strategy returns is a declaration the gate will compare, so returning
too few hides a mismatch and returning junk fails a correct tag.
"""
from __future__ import annotations

import re

CARGO_VERSION = re.compile(r"""^\s*version\s*=\s*["']([^"']+)["']""")
TABLE_RE = re.compile(r"^\s*\[\[?\s*([A-Za-z0-9_.\-]+)\s*\]?\]")

# A Swift constant that holds a version. The NAME is deliberately not pinned: one
# package calls it `marketing`, another `version`, another `VERSION` — so the
# value's SHAPE decides, not the spelling. Group 1 is the constant's name, so a
# diagnostic can say WHICH declaration it read.
SWIFT_VERSION = re.compile(
    r"""^\s*(?:public\s+|private\s+|internal\s+|fileprivate\s+|open\s+)*
        (?:static\s+)?(?:let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*
        ["']([^"']+)["']""",
    re.VERBOSE,
)
# …and the shape a RELEASE number has. A Swift version file usually holds more
# than one string constant — the marketing version AND a build number — so taking
# the FIRST declaration would read `build = "131"` on half these layouts and fail
# the gate on a correct tag. A value that is not `x.y.z` is not a release number,
# and this file's job is release numbers.
SEMVER_VALUE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?$")


def _norm(value: str) -> str:
    """A declared version and a tag name compare as bare `x.y.z`.

    A `VERSION` file may carry `v1.79.3`; a tag is `v1.79.3`. Leading `v` and
    surrounding whitespace are presentation, not identity — everything else
    (`1.79.3-rc.1` vs `1.79.3`) is a real difference and must NOT be smoothed.
    """
    return value.strip().lstrip("v").strip()


def from_version_file(text: str) -> list[tuple[str, str]]:
    """[(table, version)] for a `VERSION`-style file: its first meaningful line."""
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return [("(file)", line)]
    return []


def from_cargo(text: str) -> list[tuple[str, str]]:
    """[(table, version)] for the tables that DECLARE a release number.

    Section-aware on purpose. `version.workspace = true` in a member is an
    inheritance, and a `version` key under `[dependencies]`/`[package.metadata]`
    belongs to something else; only `workspace.package` and `package` are
    declarations of this repo's own number. Hand-rolled rather than `tomllib`
    because a hook resolves whatever `python3` is on PATH (see _gitutil).
    """
    out: list[tuple[str, str]] = []
    table = ""
    for raw in text.splitlines():
        header = TABLE_RE.match(raw)
        if header:
            table = header.group(1)
            continue
        if table not in ("workspace.package", "package"):
            continue
        m = CARGO_VERSION.match(raw)
        if m:
            out.append((table, m.group(1)))
    return out


def from_swift(text: str) -> list[tuple[str, str]]:
    """[(table, version)] for a Swift file that declares its version in a constant.

    The layout ZoneWM uses: `bump.sh` writes `Sources/ZTCore/Version.swift` and calls that the
    single source of truth, so there is no VERSION file and no Cargo.toml for this gate to read.
    SwiftPM packages commonly do this, so it is a layout rather than a quirk.

    Selection is by VALUE SHAPE, not by position, because these files carry more than one string
    constant — a marketing version AND a build number, as here — and reading the first
    declaration would take `build = "131"` and refuse a correct tag. Every `x.y.z`-shaped constant
    is returned, so a disagreement between two of them still fails the gate rather than being
    resolved by picking one; a file with no such constant declares nothing, which the caller
    already reports as a named non-run.
    """
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        m = SWIFT_VERSION.match(raw)
        if m and SEMVER_VALUE.match(m.group(2)):
            out.append((f"(swift:{m.group(1)})", m.group(2)))
    return out

# The registry. Adding a layout is one entry here, or one `kind:path` in
# GOH_TAG_VERSION_SOURCES — no new code path, no new branch to forget.
STRATEGIES = {
    "file": from_version_file,
    "cargo": from_cargo,
    "swift": from_swift,
}

# What a repo gets with no configuration: the two layouts that were live when this was written.
# `GOH_TAG_VERSION_SOURCES` REPLACES this list rather than adding to it, so a repo that names its
# own layout is reading exactly the sources it declared and no inherited surprise.
DEFAULT_SOURCES = ("file:VERSION", "cargo:Cargo.toml")
KINDS = tuple(STRATEGIES)
