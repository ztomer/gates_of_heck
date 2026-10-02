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
# A plist is not line-oriented in the way the other layouts are, but it is regular enough: `<key>`
# and `<string>` each occupy a line in every plist Xcode or PlistBuddy writes, and matching the
# whole element (rather than scraping a value after it) is what keeps a `<string>` that is not a
# version -- a bundle name, a copyright line -- from being read as one.
PLIST_ELEMENT = re.compile(r"<(?P<kind>key|string)>\s*(?P<value>[^<]*)</\s*(?P=kind)\s*>")


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


# An Xcode build-settings assignment: `KEY = value`, optionally conditioned
# (`KEY[sdk=macosx*] = value`). The condition is tolerated and not captured, because a repo
# that conditions its version on an SDK still declares exactly one version, and refusing it
# would be the gate being clever at the repo's expense.
XCCONFIG_SETTING = re.compile(
    r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[[^\]]*\])?\s*=\s*(.*?)\s*(?://.*)?$""",
    re.VERBOSE,
)


def from_xcconfig(text: str) -> list[tuple[str, str]]:
    """[(table, version)] for an Xcode build-settings file that declares the version.

    The layout a project uses when ONE file has to be readable by Xcode AND by a shell: the
    settings file is the declaration, and the Swift the app links is generated from it. Pointing
    the tag gate at the generated Swift instead would police a projection — it would agree with
    the canonical file for as long as the generator ran, and say nothing at all about a hand edit
    to the file that is actually the source.

    The `file:` strategy cannot read this layout, and reading it that way fails in the most
    misleading way available. `from_version_file` returns the first line it does not read as a
    `#` comment — and Xcode comments start with `//`, so a commented settings file hands the gate
    a sentence as its version. Strip the comment and it returns `MARKETING_VERSION = 2.73.0`
    whole, still compared against the bare tag `v2.73.0`. Both are a correct release reported as a
    mismatch. Hence a real strategy rather than a pointer at the same file.

    Selection is by VALUE SHAPE for the same reason as `swift`: these files carry a marketing
    version AND a build number (`CURRENT_PROJECT_VERSION = 131`), and the build number is not a
    release number. Every `x.y.z`-shaped setting is returned, so a file disagreeing with itself
    still fails the gate instead of being resolved by taking the first.
    """
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        m = XCCONFIG_SETTING.match(raw)
        if m and SEMVER_VALUE.match(m.group(2)):
            out.append((f"(xcconfig:{m.group(1)})", m.group(2)))
    return out


def from_plist(text: str) -> list[tuple[str, str]]:
    """[(key, version)] for an Apple bundle Info.plist.

    The layout every shipping Mac app uses, and the one this table was missing: an
    `Info.plist` carries the version as a `<string>` VALUE under a `<key>`, which is neither a
    `VERSION` line, a Cargo `version =`, a Swift `let`, nor an xcconfig setting. Measured on
    ZeroThunder, whose `v2.10.0` tag the gate could not check at all:

      `file:`     -> `('(file)', '<?xml version="1.0" encoding="UTF-8"?>')` — the XML DECLARATION,
                     read as a version, because `from_version_file` takes the first line it does
                     not read as a `#` comment and `<?xml` is not one. It then compares the tag
                     against "1.0" and reports a correct release as a mismatch.
      `swift:`    -> nothing, correctly: there is no `let` in a plist.
      `xcconfig:` -> nothing, correctly: there is no `SETTING =` in a plist.

    So a repo declaring its version the way Apple ships it was UNVERIFIABLE, and the gate's own
    rule is that an unverifiable version source is a finding rather than a pass. That is correct
    behaviour pointed at a layout nobody had written down, which is the same class as a gate that
    cannot see a member of its own population.

    Both `CFBundleShortVersionString` and `CFBundleVersion` are returned when they are `x.y.z`.
    A bundle that also carries a build NUMBER (`CFBundleVersion` is a monotonic integer, not a
    release number) is not a disagreement, and matching by VALUE SHAPE is what keeps that from
    reading as one -- the same rule as `swift:` and `xcconfig:`. Two `x.y.z` values that disagree
    still fail the gate rather than being resolved by taking the first.
    """
    out: list[tuple[str, str]] = []
    key = None
    for raw in text.splitlines():
        tag = PLIST_ELEMENT.match(raw.strip())
        if not tag:
            continue
        if tag.group(1) == "key":
            key = tag.group(2)
            continue
        if key and SEMVER_VALUE.match(tag.group(2)):
            out.append((f"(plist:{key})", tag.group(2)))
        key = None
    return out


# The registry. Adding a layout is one entry here, or one `kind:path` in
# GOH_TAG_VERSION_SOURCES — no new code path, no new branch to forget.
STRATEGIES = {
    "file": from_version_file,
    "cargo": from_cargo,
    "swift": from_swift,
    "xcconfig": from_xcconfig,
    "plist": from_plist,
}

# What a repo gets with no configuration: the two layouts that were live when this was written.
# `GOH_TAG_VERSION_SOURCES` REPLACES this list rather than adding to it, so a repo that names its
# own layout is reading exactly the sources it declared and no inherited surprise.
DEFAULT_SOURCES = ("file:VERSION", "cargo:Cargo.toml")
KINDS = tuple(STRATEGIES)
