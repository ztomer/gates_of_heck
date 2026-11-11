#!/usr/bin/env python3
"""changelog_cut.py -- a release's CHANGELOG stanza IS what Unreleased held (GOH.REL2).

goh's own CHANGELOG carried, under "Unreleased", twelve entries already shipped in v0.60.0 to
v0.63.0: each release wrote a SHORT stanza by hand and left the long entries where they were, so
Unreleased claimed as unreleased what four tags had shipped. At a tagged commit nothing is
unreleased -- the tag ships the whole tree -- so:

* `cut --version X [--title T]`: moves Unreleased's body into a new `## vX -- T` stanza, in the
  file's own heading style (` — ` and a ` _(YYYY-MM-DD)_` day when the stanzas carry them) directly
  under the (now empty) Unreleased heading. The title is T, else the first entry's first clause; a
  file whose stanzas carry no title gets a bare `## vX`. Every Unreleased section is folded in and
  ONE heading is left. When a stanza for X already exists (written by hand), Unreleased's entries
  are merged into it, by sub-heading, and an entry it already holds is not repeated (GOH.CUTTITLE).
  release.sh runs it itself whenever Unreleased holds entries (GOH.CUTAUTO). Refused when
  Unreleased is empty (nothing to release) or when a moved entry repeats one an earlier stanza holds.
* `check --version X`: what release.sh's changelog step asks of the commit being tagged -- the
  stanza for X exists, its heading's title is a phrase (it opens on a word, a code span or a quote,
  does not end on a joining mark, leaves no backtick or bracket open: AU.RELHEADING; `cut` refuses
  the same title, given or derived), Unreleased (if the file keeps one) is ONE section and EMPTY,
  and no entry of X's stanza repeats an entry of an earlier stanza. Exit 1 names each finding.
* `ready --version X [--title T]`: goh ship's preflight, asked BEFORE its push (GOH.SHIPPREFLIGHT):
  exit 1 when a cut of X would be refused as empty (`nothing_to_release`) or its title, T or the
  derived one, is no phrase or a clause clipped at TITLE_MAX (GOH.SHIPSUMMARY), else 0.

An entry is a top-level list item (`* ` or `- ` at column 0) with its continuation lines, compared
with whitespace collapsed. Headings: `## Unreleased` / `## [Unreleased]`, `## vX...` / `## [X]...`.

    changelog_cut.py cut   --version 1.2.3 [--title "what it ships"] [--file CHANGELOG.md]
    changelog_cut.py check --version 1.2.3 [--file CHANGELOG.md]
    changelog_cut.py ready --version 1.2.3 [--title "what it ships"] [--file CHANGELOG.md]
"""

from __future__ import annotations

import argparse
import datetime
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
    n_unrel = sum(1 for s in secs if s.unreleased)
    if n_unrel > 1:
        findings.append(
            f"the Unreleased heading appears twice or more ({n_unrel}): it is ONE section -- run "
            f"changelog_cut.py cut, which folds them into the stanza and leaves one heading"
        )
    for s in secs:
        held = [ln for ln in body(lines, s) if ln.strip()]
        if s.unreleased and held:
            findings.append(
                f"'{s.title}' still holds {len(held)} line(s) at the commit being tagged: the tag "
                f"ships the whole tree, so they are released -- move them into the v{version} "
                f"stanza (changelog_cut.py cut) or delete what an earlier stanza already says"
            )
    findings += [f"duplicate entry: {d}" for d in duplicates(lines, secs, version)]
    mine = next(s for s in secs if s.version == version)
    why = title_findings(heading_title(mine.title))
    if why:
        findings.append(_fragment(mine.title, why))
    return findings


def _fragment(heading: str, why: list[str]) -> str:
    return (
        f"the heading '{heading}' is not a phrase: {'; '.join(why)} -- a release is named by a "
        f"title that stands alone (pass --title, or write the heading by hand)"
    )


DATED_TAIL = re.compile(r"\s_\(\d{4}-\d{2}-\d{2}\)_$")
STYLE_WINDOW = 5  # how many of the newest titled stanzas say whether the file dates its headings


def heading_style(secs: list[Section]) -> tuple[bool, str, bool]:
    """(titled, separator, dated) of the file's version headings: routines and ztools write
    `## vX — title _(YYYY-MM-DD)_`, goh `## vX -- title`. A cut follows the file it joins. The day
    is the house heading's, so ANY of the newest few titled stanzas carrying one makes the file
    dated: a hand-written `## vX -- title` without it is a lapse, not a change of style."""
    titled = [s for s in secs if s.version and len(s.title.split(maxsplit=1)) > 1]
    if not titled:
        return False, "--", False
    dated = [s for s in titled[:STYLE_WINDOW] if DATED_TAIL.search(s.title)]
    ref = (dated or titled)[0].title.split(maxsplit=1)[1]
    return True, ("—" if ref.startswith("— ") else "--"), bool(dated)


TITLE_STOP = re.compile(r"\s\(|:\s|\.\s|;\s|\s--\s|\s—\s|\.$")
TITLE_MAX = 90


def derive_title(first_entry: str) -> str:
    """A heading's title from an entry: its first clause, house-lowercase, at most TITLE_MAX."""
    return derive(first_entry)[0]


def derive(first_entry: str) -> tuple[str, str | None]:
    """(title, why it is no title or None). The title is the entry's first clause, house-lowercase.
    A bold lead that is its own sentence (`**goh ship releases goh itself.** goh declared ...`) IS
    the title; a label lead (`**Fix**: the x`) gives way to what follows it, unless that is no
    phrase (AU.RELHEADING). A clause longer than TITLE_MAX is not cut to fit: v0.96.0 shipped as
    `## v0.96.0 -- ... and v0.95.0's bump was made by`, a clause cut at 90 characters
    (GOH.SHIPSUMMARY), so a clipped clause comes back with the reason, and `cut` refuses it,
    naming --title."""
    text = first_entry.strip()
    lead = re.match(r"^\*\*([^*]+)\*\*:?\s*(.*)$", text)
    if lead:
        own_sentence = lead.group(1).rstrip().endswith((".", "!", "?"))
        text = (
            lead.group(2)
            if re.match(r"[\w`'\"]", lead.group(2)) and not own_sentence
            else lead.group(1).strip()
        )
    m = TITLE_STOP.search(text)
    clause = (text[: m.start()] if m else text).strip()
    clipped = None
    if len(clause) > TITLE_MAX:
        clipped = (
            f"the first entry's first clause runs {len(clause)} characters, past {TITLE_MAX}, and "
            f"cut to fit it would break off mid-clause"
        )
        clause = clause[:TITLE_MAX].rsplit(" ", 1)[0]
        if clause.count("`") % 2:  # never leave a code span open
            clause = clause[: clause.rfind("`")]
    if clause[:2].isalpha() and clause[1:2].islower():  # "The x" -> "the x"; "ZT.X" stays
        clause = clause[:1].lower() + clause[1:]
    return clause.rstrip(" ,;:"), clipped


_HEADING_TAIL = re.compile(r"(?:^|\s)(?:_?\(\d{4}-\d{2}-\d{2}\)_?|-?\s*\d{4}-\d{2}-\d{2})$")
_HEADING_HEAD = re.compile(r"^(?:v|\[)\d+\.\d+(?:\.\d+)?\]?\s*(?:--|—|\u2013|-)?\s*")
_DANGLING_END = ",;:-—\u2013("


def heading_title(heading_text: str) -> str:
    """The title of a version heading's text (`v1.2.3 — title _(day)_`); '' for a bare heading."""
    return _HEADING_TAIL.sub("", _HEADING_HEAD.sub("", heading_text, count=1)).strip()


def title_findings(title: str) -> list[str]:
    """Why `title` is not a phrase (AU.RELHEADING: app_updates v1.59.1 shipped `## v1.59.1 — , like
    every other config file`): it must open on a word, a code span or a quote, close on something
    other than a joining mark, and balance its backticks and brackets."""
    out: list[str] = []
    if not title:
        return out
    if not re.match(r"[\w`'\"]", title):
        out.append("it opens on punctuation, so it is the tail of a sentence")
    if title[-1] in _DANGLING_END:
        out.append(f"it ends on '{title[-1]}', so it breaks off")
    if (
        title.count("`") % 2
        or title.count("(") != title.count(")")
        or title.count("[") != title.count("]")
    ):
        out.append("a backtick, parenthesis or bracket is left open")
    return out


def _strip_edges(ls: list[str]) -> list[str]:
    while ls and not ls[0].strip():
        ls = ls[1:]
    while ls and not ls[-1].strip():
        ls = ls[:-1]
    return ls


def _rstrip(ls: list[str]) -> list[str]:
    while ls and not ls[-1].strip():
        ls = ls[:-1]
    return ls


def _split_subs(ls: list[str], level: int) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """A body as (preamble, [(sub-heading line, its lines)]) at heading level `level`."""
    pre: list[str] = []
    subs: list[tuple[str, list[str]]] = []
    for line in ls:
        m = HEADING.match(line)
        if m and len(m.group(1)) == level:
            subs.append((line, []))
        elif subs:
            subs[-1][1].append(line)
        else:
            pre.append(line)
    # A sub-heading keeps the blank (or none) its author put after it; only its tail is trimmed.
    return _strip_edges(pre), [(h, _rstrip(b)) for h, b in subs]


def _drop_held(ls: list[str], held: set[str]) -> list[str]:
    """`ls` without the top-level entries (with their continuation lines) whose text is in `held`."""
    groups: list[list[str]] = []
    for line in ls:
        if (
            line.startswith(("* ", "- "))
            or not groups
            or not (
                line.startswith((" ", "\t"))
                and line.strip()
                and groups[-1][0].startswith(("* ", "- "))
            )
        ):
            groups.append([line])
        else:
            groups[-1].append(line)
    return [
        ln
        for g in groups
        if not (g[0].startswith(("* ", "- ")) and entries(g)[0] in held)
        for ln in g
    ]


def merge_into_stanza(base: list[str], st: Section, moved: list[str]) -> list[str]:
    """`base` with `moved` merged into the hand-written stanza `st`: same-named sub-headings are
    appended to, new ones added after, and an entry the stanza already holds is not repeated."""
    sub_level = st.level + 1
    own = body(base, st)
    pre, subs = _split_subs(own, sub_level)
    mpre, msubs = _split_subs(_drop_held(moved, set(entries(own))), sub_level)
    pre = _strip_edges([*pre, *mpre]) if not (pre and mpre) else [*pre, *mpre]
    for head, mbody in msubs:
        name = head.lstrip("#").strip().lower()
        for k, (h, b) in enumerate(subs):
            if h.lstrip("#").strip().lower() == name:
                subs[k] = (h, [*b, *_strip_edges(mbody)])
                break
        else:
            subs.append((head, mbody))
    block: list[str] = [""]
    if pre:
        block += [*pre, ""]
    for h, b in subs:
        block += [h, *b, ""]
    return [*base[: st.start + 1], *block, *base[st.end :]]


def nothing_to_release(lines: list[str], version: str) -> bool:
    """True when a cut of VERSION would be refused as empty: the file has an Unreleased section,
    every one is empty, and no stanza for VERSION is written yet (goh ship asks before its push,
    GOH.SHIPUNRELEASED). A file with no Unreleased heading does not cut from one: False."""
    secs = sections(lines)
    unrels = [s for s in secs if s.unreleased]
    if not unrels or any(s.version == version for s in secs):
        return False
    return not any(_strip_edges(body(lines, u)) for u in unrels)


def title_refusal(lines: list[str], version: str, title: str | None) -> str | None:
    """Why a cut of VERSION would refuse its heading's title ('' title given or derived), or None.
    Asked by `ready`, before goh ship's push, of what the kit's cut will write."""
    secs = sections(lines)
    unrels = [s for s in secs if s.unreleased]
    if not unrels or any(s.version == version for s in secs):
        return None
    if title is None:
        if not heading_style(secs)[0]:
            return None
        moved = [ln for u in unrels for ln in body(lines, u)]
        title, clipped = derive((entries(moved) or [""])[0])
        if clipped:
            return f"the derived v{version} title '{title}' is not a phrase: {clipped}"
    why = title_findings(title)
    return f"the v{version} title '{title}' is not a phrase: {'; '.join(why)}" if why else None


def cut(lines: list[str], version: str, title: str | None) -> tuple[list[str] | None, list[str]]:
    secs = sections(lines)
    unrels = [s for s in secs if s.unreleased]
    if not unrels:
        return None, ["no Unreleased section to cut from"]
    moved: list[str] = []
    for u in unrels:
        part = _strip_edges(body(lines, u))
        if part:
            moved += ([""] if moved else []) + part
    first = unrels[0]
    if not moved and not (len(unrels) > 1 and any(s.version == version for s in secs)):
        return None, ["Unreleased is empty: nothing to release"]
    # One Unreleased heading, emptied: the other Unreleased sections' lines go with their headings.
    gone = {i for u in unrels[1:] for i in range(u.start, u.end)}
    base: list[str] = []
    head_at = 0
    for i, line in enumerate(lines):
        if i in gone or first.start < i < first.end:
            continue
        base.append(line)
        if i == first.start:
            head_at = len(base) - 1
            base.append("")
    exists = next((s for s in sections(base) if s.version == version), None)
    if exists is not None:
        new = merge_into_stanza(base, exists, moved) if moved else base
    else:
        titled, sep, dated = heading_style(secs)
        head = "#" * first.level
        clipped = None
        if title is None and titled:
            title, clipped = derive((entries(moved) or [""])[0])
        stamp = f" _({datetime.date.today().isoformat()})_" if dated else ""
        why = title_findings(title or "") + ([clipped] if clipped else [])
        if why:
            return None, [_fragment(title or "", why)]
        heading = f"{head} v{version} {sep} {title}{stamp}" if title else f"{head} v{version}"
        new = [*base[: head_at + 2], heading, "", *moved, "", *base[head_at + 2 :]]
    dups = duplicates(new, sections(new), version)
    if dups:
        return None, [f"duplicate entry: {d}" for d in dups]
    return new, []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("mode", choices=["cut", "check", "ready"])
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
    if a.mode == "ready":  # goh ship's preflight, before its push (GOH.SHIPPREFLIGHT)
        if nothing_to_release(lines, a.version):
            print(f"✗ changelog: {path}: Unreleased is empty and no ## v{a.version} stanza is "
                  "written; add the entry this release ships under ## Unreleased, commit, then "
                  "ship", file=sys.stderr)  # fmt: skip
            return 1
        # '' is no title: goh ship passes its --summary, empty when none was given.
        why = title_refusal(lines, a.version, a.title or None)
        if why:  # the kit's cut would refuse it after the push (GOH.SHIPSUMMARY)
            print(f"✗ changelog: {path}: {why}; pass `goh ship --summary TEXT`, which titles "
                  "the stanza", file=sys.stderr)  # fmt: skip
            return 1
        return 0
    if a.mode == "check":
        findings = check(lines, a.version)
        for f in findings:
            print(f"✗ changelog: {f}", file=sys.stderr)
        return 1 if findings else 0
    new, findings = cut(lines, a.version, a.title)
    for f in findings:
        print(f"✗ changelog_cut: {f}", file=sys.stderr)
    if new is None:
        return 1
    path.write_text("\n".join(new) + "\n", encoding="utf-8")
    print(f"✓ {path}: Unreleased is now the v{a.version} stanza (one heading)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
