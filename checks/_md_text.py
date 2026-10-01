"""Markdown text -> the links it contains and the anchors it generates.

Pure: no I/O, no git, no repo. Split out of `check_md_links.py` because this is
the part that is worth reasoning about in isolation and the part most likely to
be re-implemented by hand — and a hand-rolled anchor derivation is exactly the
defect `check_md_links.py` exists to end.

Every rule here was written by being wrong first, and the comment on each says
what getting it wrong costs. Three of them are non-obvious enough to be worth
stating once, here, rather than in a caller:

* **An anchor is not the heading.** GitHub lowercases, drops every character
  that is not alphanumeric, a space, a hyphen or an underscore, and turns spaces
  into hyphens. `### 4.8. What `.90` can actually do, measured` becomes
  `#48-what-90-can-actually-do-measured`.
* **Code is not content.** Fenced blocks, INDENTED blocks and inline code spans
  are skipped for LINK extraction — documentation about markdown link syntax
  contains links meant not to resolve — but NOT for anchor extraction, where a
  code span inside a heading contributes its INNER text to the slug.
* **Duplicates are numbered.** A link to the second `## Notes` resolves to
  `#notes-1`, so a set, not a list.
"""
from __future__ import annotations

import re
import unicodedata

# `[text](target)` and `![alt](target)`. The target stops at whitespace or a
# closing paren, which is the whole of CommonMark's inline-link target: angle
# brackets may wrap it, and a title follows it.
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'(][^)]*)?\s*\)")
# Reference definitions: `[label]: target`.
REFERENCE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^>\s]+)>?")
# ATX headings, and setext underlines (a run of `=` or `-` under a line).
ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT = re.compile(r"^\s{0,3}(=+|-+)\s*$")
# Fenced blocks: ``` or ~~~ with an optional info string.
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# An INDENTED code block: four spaces or a tab, per CommonMark.
INDENTED = re.compile(r"^(?: {4}|\t)")
# Inline code spans, so `[x](y)` inside one is an example, not a link.
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)*?\1", re.DOTALL)
# An explicit anchor: `{#id}` at the end of a heading, or a raw HTML anchor.
EXPLICIT_ID = re.compile(r"\{#([^}\s]+)\}")
HTML_ANCHOR = re.compile(
    r"<a\s+(?:id|name)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
# A URL with a scheme. Out of scope by definition: nothing here can know whether
# a remote document still exists.
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def strip_fences(text: str) -> str:
    """Blank out FENCED and INDENTED code blocks, keeping line structure.

    Line numbers have to survive: a finding is reported as `file:line`, and a
    checker that renumbered the lines would report every finding against the
    wrong one.

    Fences only, and deliberately NOT inline code spans: a heading's own text may
    contain one, and a code span inside a heading contributes its INNER text to
    the anchor. Blanking it here turned `4.8. What `.90` can actually do` into
    `48-what-------can-actually-do`, and the gate rejected the very link it was
    written to check.
    """
    out: list[str] = []
    fence: str | None = None
    indented = False
    for raw in text.splitlines():
        hit = FENCE.match(raw)
        if fence is not None:
            out.append("")
            if hit and hit.group(1)[0] == fence[0] and len(hit.group(1)) >= len(fence):
                fence = None
            continue
        if hit:
            fence = hit.group(1)
            out.append("")
            continue
        # An INDENTED code block: four spaces or a tab, ending at the first blank
        # line. Both fences and indentation are code blocks in CommonMark, and
        # handling only fences reports every indented example in every doc as a
        # broken link.
        if INDENTED.match(raw):
            indented = True
            out.append("")
            continue
        if indented:
            if not raw.strip():
                indented = False
            out.append("")
            continue
        out.append(raw)
    return "\n".join(out)


def strip_code(text: str) -> str:
    """Fenced and indented blocks AND inline code spans, keeping line structure.

    This is the LINK pass's view, where an example must not resolve.
    """
    return "\n".join(
        CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)
        for line in strip_fences(text).splitlines()
    )


def slugify(heading: str) -> str:
    """GitHub's heading anchor, as `github-slugger` computes it.

    Accented letters SURVIVE, because GitHub keeps them (`café` → `café`, not
    `caf`). Normalising to ASCII here would invent a broken anchor for every
    non-English heading in the estate — the precise failure this checker exists
    to end, manufactured by the checker.
    """
    text = re.sub(r"<[^>]+>", "", heading)          # inline HTML in the heading
    text = re.sub(r"[`*_~]", "", text)               # its own emphasis markers
    text = text.replace("\t", " ")
    text = unicodedata.normalize("NFC", text)
    kept = "".join(ch for ch in text.lower() if ch.isalnum() or ch in " -_")
    return kept.strip().replace(" ", "-")


def anchors(text: str) -> set[str]:
    """Every anchor this markdown file generates.

    Duplicates get GitHub's `-1`, `-2` counters, because a link to the second
    `## Notes` has to resolve and the bare slug resolves to the first.
    """
    out: set[str] = set()
    seen: dict[str, int] = {}
    lines = strip_fences(text).splitlines()
    for i, raw in enumerate(lines):
        atx = ATX.match(raw)
        body = None
        if atx:
            body = atx.group(2)
        elif i + 1 < len(lines) and raw.strip() and SETEXT.match(lines[i + 1]):
            body = raw.strip()                      # setext: text on the line above
        if body is None:
            for hit in HTML_ANCHOR.finditer(raw):
                out.add(hit.group(1))
            continue
        explicit = EXPLICIT_ID.search(body)
        if explicit:
            out.add(explicit.group(1))
            body = body[: explicit.start()]
        slug = slugify(body)
        if not slug:
            continue
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        out.add(slug if count == 0 else f"{slug}-{count}")
    return out


def split_target(target: str) -> tuple[str, str]:
    """(path, fragment). A bare `#frag` is the same file."""
    path, sep, fragment = target.partition("#")
    return path, fragment if sep else ""


def classify(target: str) -> tuple[str, str]:
    """(verdict, why) for one link target. verdict: check | skip."""
    if SCHEME.match(target):
        return "skip", "has a scheme"
    if target.startswith("//"):
        return "skip", "protocol-relative"
    if target.startswith("/"):
        return "skip", "site-absolute"
    return "check", ""


def links_in(text: str) -> list[tuple[int, str]]:
    """[(line_number, target)] for every link in one markdown file."""
    cleaned = strip_code(text)
    out: list[tuple[int, str]] = []
    for number, raw in enumerate(cleaned.splitlines(), 1):
        for hit in LINK.finditer(raw):
            out.append((number, hit.group(1)))
        ref = REFERENCE.match(raw)
        if ref:
            out.append((number, ref.group(1)))
    return out