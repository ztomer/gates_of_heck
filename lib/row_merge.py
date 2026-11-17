"""lib/row_merge.py: `goh-rows`, the merge driver that merges a registry by ROW (GOH.LANDUNION).

OWNER, 2026-11-17: "we need to optimize the overall process." Every goh item appends to the same
registries -- docs/map.md and docs/config.md rows, gates/verdict_free_keys.txt, the house-script
subcommand table -- and two lanes landing back to back conflicted there although their rows were
independent: 3 reds that day (LANDREPORT against AFFECTEDBINARY2: two ADJACENT bullets of
docs/map.md, each edited by one side), each a full queue wait to rebase and re-queue.

git's built-in `merge=union` is not enough: it keeps both sides of a conflicting hunk, so two edits
to adjacent rows come out as four rows (each edited row beside the other side's stale copy), and two
edits to ONE row come out as two rows, merged silently. This driver merges by KEY instead:

  * a line's key: a markdown table row's first cell (first and second when the first is a
    placeholder like `(structural step)`; a header row, the one above a `|---|` line, has none), a
    bullet's leading backticked name (`* `tools/land.sh` -- ...`); in any other file the first
    whitespace-separated word of a line that is not blank or a `#` comment. Every other line
    (prose, headings, separators, blank lines) is UNKEYED;
  * git's own text merge first (`git merge-file`): a clean text merge is taken as git makes it;
  * on a text conflict, by key: one side's unkeyed lines must equal the base's (else no merge), and
    that side's row changes are applied to the other side: a row it edited replaces the base's, one
    it deleted goes, one it added is placed after the row it followed (or before the row it
    preceded). Two different edits to ONE row, an edit against a delete, or a key that is not
    unique in base, ours or theirs is a CONFLICT: git's conflict markers are written, exit 1.

`--check FILE...` is the registries' own gate: exit 1 naming each key a file holds twice (the shape
a union or a hand merge leaves behind). Registered by `.gitattributes` (`merge=goh-rows`) and, for a
rebase, by `tools/land.sh` (`-c merge.goh-rows.driver=...`); where no driver is defined git merges
the file as text, so a missing registration conflicts as before and never merges wrongly.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import re
import subprocess
import sys
from pathlib import Path

Key = tuple[str, ...]
SEPARATOR = re.compile(r"^\|(?:\s*:?-+:?\s*\|)+\s*$")
BULLET = re.compile(r"^\*\s+(`[^`]+`)")
COMMENT = re.compile(r"^\s*(?:#|$)")


def keys(lines: list[str], markdown: bool) -> list[Key | None]:
    """Each line's key, or None for an unkeyed line."""
    out: list[Key | None] = []
    for i, line in enumerate(lines):
        text = line.rstrip("\r\n")
        if not markdown:
            out.append(None if COMMENT.match(text) else ("word", text.split()[0]))
            continue
        bullet = BULLET.match(text)
        if bullet:
            out.append(("bullet", bullet.group(1)))
            continue
        nxt = lines[i + 1].rstrip("\r\n") if i + 1 < len(lines) else ""
        if not text.startswith("|") or SEPARATOR.match(text) or SEPARATOR.match(nxt):
            out.append(None)
            continue
        cells = [c.strip() for c in text.strip().strip("|").split("|")]
        out.append(("row", *cells[:2]) if cells[0].startswith("(") else ("row", cells[0]))
    return out


def is_markdown(path: str) -> bool:
    return path.endswith(".md")


def duplicates(lines: list[str], markdown: bool) -> list[Key]:
    """The keys `lines` holds more than once, in order of their second appearance."""
    seen: set[Key] = set()
    dup: list[Key] = []
    for k in keys(lines, markdown):
        if k is not None:
            if k in seen and k not in dup:
                dup.append(k)
            seen.add(k)
    return dup


def _index(lines: list[str], markdown: bool) -> dict[Key, str] | None:
    """{key: its line}, or None when a key repeats (no merge by key is sound then)."""
    out: dict[Key, str] = {}
    for k, line in zip(keys(lines, markdown), lines, strict=True):
        if k is not None:
            if k in out:
                return None
            out[k] = line
    return out


def _unkeyed(lines: list[str], markdown: bool) -> list[str]:
    return [line for k, line in zip(keys(lines, markdown), lines, strict=True) if k is None]


def _at(lines: list[str], markdown: bool, key: Key) -> int:
    return keys(lines, markdown).index(key)


def by_key(base: list[str], ours: list[str], theirs: list[str], markdown: bool) -> list[str] | None:
    """The row-wise merge of `ours` and `theirs` from `base`, or None when it is a conflict."""
    ko, ka, kb = (_index(x, markdown) for x in (base, ours, theirs))
    if ko is None or ka is None or kb is None:
        return None
    unkeyed = _unkeyed(base, markdown)
    if _unkeyed(theirs, markdown) != unkeyed:
        if _unkeyed(ours, markdown) != unkeyed:
            return None
        ours, theirs, ka, kb = theirs, ours, kb, ka
    out = list(ours)
    for key, was in ko.items():
        now, mine = kb.get(key), ka.get(key)
        if now in (was, mine):
            continue
        if mine != was:  # both sides changed this row, differently (an edit, or one deleted it)
            return None
        at = _at(out, markdown, key)
        if now is None:
            del out[at]
        else:
            out[at] = now
    theirs_keys = keys(theirs, markdown)
    for i, key in enumerate(theirs_keys):
        if key is None or key in ko:
            continue
        if key in ka:
            if ka[key] != kb[key]:
                return None
            continue
        placed = _keys_set(out, markdown)
        prev = theirs_keys[i - 1] if i > 0 else None
        nxt = theirs_keys[i + 1] if i + 1 < len(theirs_keys) else None
        if prev is not None and prev in placed:
            out.insert(_at(out, markdown, prev) + 1, theirs[i])
        elif nxt is not None and nxt in placed:
            out.insert(_at(out, markdown, nxt), theirs[i])
        else:
            return None
    return out


def _keys_set(lines: list[str], markdown: bool) -> set[Key]:
    return {k for k in keys(lines, markdown) if k is not None}


def _read(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines(keepends=True)


def driver(base: str, ours: str, theirs: str, path: str) -> int:
    """git's merge driver contract: the result in `ours`, exit 0 clean, 1 a conflict."""
    text = subprocess.run(
        [
            *(
                "git",
                "merge-file",
                "-p",
                "-L",
                f"ours:{path}",
                "-L",
                "base",
                "-L",
                f"theirs:{path}",
            ),
            *(ours, base, theirs),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if text.returncode < 0 or text.returncode > 127:
        sys.stderr.write(text.stderr)
        return 2
    if text.returncode == 0:
        Path(ours).write_text(text.stdout, encoding="utf-8")
        return 0
    merged = by_key(_read(base), _read(ours), _read(theirs), is_markdown(path))
    if merged is None:
        Path(ours).write_text(text.stdout, encoding="utf-8")
        sys.stderr.write(f"goh-rows: {path}: two edits to one row, or a row key that repeats\n")
        return 1
    Path(ours).write_text("".join(merged), encoding="utf-8")
    return 0


def check(paths: list[str]) -> int:
    """Exit 1 naming every key a registry holds twice."""
    bad = 0
    for path in paths:
        for key in duplicates(_read(path), is_markdown(path)):
            sys.stderr.write(f"{path}: the key {' | '.join(key[1:])} is on two lines\n")
            bad = 1
    return bad


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        return check(argv[1:])
    if len(argv) != 4:
        sys.stderr.write("usage: row_merge.py BASE OURS THEIRS PATH | --check FILE...\n")
        return 2
    return driver(*argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
