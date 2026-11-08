#!/usr/bin/env python3
"""changelog_cut.py -- a release's CHANGELOG stanza IS what Unreleased held (GOH.REL2).

goh's own CHANGELOG carried, under "Unreleased", twelve entries already shipped in v0.60.0 to
v0.63.0: each release wrote a SHORT stanza by hand and left the long entries where they were, so
Unreleased claimed as unreleased what four tags had shipped. At a tagged commit nothing is
unreleased -- the tag ships the whole tree -- so:

* `cut --version X --title T`: moves Unreleased's body into a new `## vX -- T` stanza directly
  under the (now empty) Unreleased heading. Refused when the stanza exists, when Unreleased is
  empty (nothing to release), or when a moved entry repeats one an earlier stanza already holds.
* `check --version X`: what release.sh's changelog step asks of the commit being tagged -- the
  stanza for X exists, Unreleased (if the file keeps one) is EMPTY, and no entry of X's stanza
  repeats an entry of an earlier stanza. Exit 1 names each finding.

An entry is a top-level list item (`* ` or `- ` at column 0) with its continuation lines, compared
with whitespace collapsed. Headings: `## Unreleased` / `## [Unreleased]`, `## vX...` / `## [X]...`.

    changelog_cut.py cut   --version 1.2.3 --title "what it ships" [--file CHANGELOG.md]
    changelog_cut.py check --version 1.2.3 [--file CHANGELOG.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#+)\s+(.*?)\s*$")
UNRELEASED = re.compile(r"^\[?unreleased\]?(\s|$)", re.IGNORECASE)


def _version_of(title: str) -> str | None:
    m = re.match(r"^(?:v|\[)(\d+\.\d+(?:\.\d+)?)(?:\]|\s|$)", title)
    return m.group(1) if m else None


class Section:
    def __init__(self, level: int, title: str, start: int) -> None:
        self.level, self.title, self.start, self.end = level, title, start, start + 1

    @property
    def version(self) -> str | None:
        return _version_of(self.title)

    @property
    def unreleased(self) -> bool:
        return bool(UNRELEASED.match(self.title))


def sections(lines: list[str]) -> list[Section]:
    """Every level-2+ heading's section: from it to the next heading at its level or above."""
    out: list[Section] = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m and len(m.group(1)) >= 2:
            out.append(Section(len(m.group(1)), m.group(2), i))
    for k, s in enumerate(out):
        nxt = next((t.start for t in out[k + 1 :] if t.level <= s.level), len(lines))
        s.end = nxt
    return out


def body(lines: list[str], s: Section) -> list[str]:
    return lines[s.start + 1 : s.end]


def entries(text_lines: list[str]) -> list[str]:
    """Top-level list items, each with its continuation lines, whitespace collapsed."""
    out: list[list[str]] = []
    open_entry = False
    for line in text_lines:
        if line.startswith(("* ", "- ")):
            out.append([line[2:]])
            open_entry = True
        elif open_entry and line.startswith((" ", "\t")) and line.strip():
            out[-1].append(line)
        elif line.strip():
            open_entry = False  # a heading or a paragraph ends the entry above it
    return [" ".join(" ".join(e).split()) for e in out]


def duplicates(lines: list[str], secs: list[Section], version: str) -> list[str]:
    """Entries of `version`'s stanza that an earlier (other) version stanza already holds."""
    mine = next(s for s in secs if s.version == version)
    held: dict[str, str] = {}
    for s in secs:
        if s.version and s.version != version and s.level == mine.level:
            for e in entries(body(lines, s)):
                held.setdefault(e, s.title.split()[0])
    return [f"{e[:90]}... already in {held[e]}" for e in entries(body(lines, mine)) if e in held]


def check(lines: list[str], version: str) -> list[str]:
    secs = sections(lines)
    findings: list[str] = []
    if not any(s.version == version for s in secs):
        return [f"no stanza for {version} (## v{version} ...)"]
    for s in secs:
        held = [ln for ln in body(lines, s) if ln.strip()]
        if s.unreleased and held:
            findings.append(
                f"'{s.title}' still holds {len(held)} line(s) at the commit being tagged: the tag "
                f"ships the whole tree, so they are released -- move them into the v{version} "
                f"stanza (changelog_cut.py cut) or delete what an earlier stanza already says"
            )
    findings += [f"duplicate entry: {d}" for d in duplicates(lines, secs, version)]
    return findings


def cut(lines: list[str], version: str, title: str) -> tuple[list[str] | None, list[str]]:
    secs = sections(lines)
    if any(s.version == version for s in secs):
        return None, [f"a stanza for {version} already exists"]
    unrel = next((s for s in secs if s.unreleased), None)
    if unrel is None:
        return None, ["no Unreleased section to cut from"]
    moved = body(lines, unrel)
    while moved and not moved[0].strip():
        moved = moved[1:]
    while moved and not moved[-1].strip():
        moved = moved[:-1]
    if not moved:
        return None, ["Unreleased is empty: nothing to release"]
    head = "#" * unrel.level
    new = [*lines[: unrel.start + 1], "", f"{head} v{version} -- {title}", "", *moved, ""]
    new += lines[unrel.end :]
    dups = duplicates(new, sections(new), version)
    if dups:
        return None, [f"duplicate entry: {d}" for d in dups]
    return new, []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("mode", choices=["cut", "check"])
    ap.add_argument("--version", required=True)
    ap.add_argument("--title")
    ap.add_argument("--file", default="CHANGELOG.md")
    a = ap.parse_args(argv)
    a.version = a.version.removeprefix("v")  # the tag spelling; stanzas are written `## v<version>`
    path = Path(a.file)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        print(f"✗ changelog_cut: cannot read {path}: {e}", file=sys.stderr)
        return 2
    if a.mode == "check":
        findings = check(lines, a.version)
        for f in findings:
            print(f"✗ changelog: {f}", file=sys.stderr)
        return 1 if findings else 0
    if not a.title:
        print("✗ changelog_cut: cut needs --title", file=sys.stderr)
        return 2
    new, findings = cut(lines, a.version, a.title)
    for f in findings:
        print(f"✗ changelog_cut: {f}", file=sys.stderr)
    if new is None:
        return 1
    path.write_text("\n".join(new) + "\n", encoding="utf-8")
    print(f"✓ {path}: Unreleased is now the v{a.version} stanza")
    return 0


if __name__ == "__main__":
    sys.exit(main())
