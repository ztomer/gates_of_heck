"""The queue items a subagent spawn is for, read from its brief (GOH.SPAWNKEY).

The spawn ledger took an item only from a description that starts with one, which 48 of 188 spawns
did in the three days to 2026-11-25, so spend per item could not be computed. A brief says its
items in its own words, the first of these that names any:

* the `Items:` line, the one form that is exact: `Items: GOH.A, GOH.B`, or `Items: none because
  <reason>` for work that is no item's. Nothing else is looked at;
* the ids in the description (`X.Y: what`, `goh lane X.Y`);
* `item X.Y`, `lane for X.Y`, `Item: X.Y` and their lists (`items X.Y, then X.Z`), the first such;
* lines of a list that start with an id (`1. X.Y -- what`);
* the brief's lead line (`You close X.Y in ..`), its first id alone.

A brief that names none is recorded with why (`no_item_why`), never refused: agent_guard.py writes
both on the spawn's row, spawn_register.py registers an `Items:` line's items.

the items of a spawn from its brief: `declared(prompt)` its `Items:` line alone, `fields(prompt,
description)` the spawn row's `items` and `no_item_why`. Pinned by `test_spawn_items.py`.
"""

from __future__ import annotations

import re

# `GOH.A`, `CG.2.N5d`, `COVUP.APP_UPDATES`; never a file (`AGENTS.md`): a segment starts in capitals
_ID = r"[A-Z][A-Z0-9]*(?:\.[A-Z0-9][A-Za-z0-9_]*)+"
ID = re.compile(rf"(?<![A-Za-z0-9_.-]){_ID}(?![A-Za-z0-9_])")
_MARK = r"[ \t>*`-]*"
LINE = re.compile(rf"^{_MARK}Items:{_MARK}(none\b.*|{_ID}.*)$", re.MULTILINE)
_AND = r"(?:\s*,\s*(?:then\s+|and\s+)?|\s+(?:and|then|\+|&)\s+)"
_LIST = rf"`?{_ID}`?(?:{_AND}`?{_ID}`?)*"
PHRASE = re.compile(rf"\b(?:[Ii]tems?(?::|\s+is)?|[Ll]ane\s+for)\s+({_LIST})(?![A-Za-z0-9_])")
BULLET = re.compile(rf"^[ \t]*(?:[-*]|\d+[.)])[ \t]+[*`]*({_ID})(?![A-Za-z0-9_])", re.MULTILINE)
HEADER = re.compile(rf"^{_MARK}(?:Model-choice|Pool|Agent-type|Owner-asked):")
NONE = "the brief names no item (no `Items:` line)"


def _ids(text: str) -> list[str]:
    return list(dict.fromkeys(ID.findall(text)))


def first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200]


def declared(prompt: str) -> tuple[list[str], str | None] | None:
    """The `Items:` line's (items, why there is none); None when the brief has no such line."""
    if (m := LINE.search(prompt)) is None:
        return None
    if m.group(1).startswith("none"):
        why = re.sub(r"^none\W*(?:because\b)?\W*", "", m.group(1)).strip()
        return [], why or "the brief says `Items: none`"
    return _ids(m.group(1)), None


def named(prompt: str, description: object = None) -> list[str]:
    """The items a brief with no `Items:` line names, in its own words."""
    if isinstance(description, str) and (titled := _ids(description)):
        return titled
    lead = "\n".join(ln for ln in prompt.splitlines() if not HEADER.match(ln))
    if m := PHRASE.search(lead):
        return _ids(m.group(1))
    return list(dict.fromkeys(BULLET.findall(lead))) or _ids(first_line(lead))[:1]


def fields(prompt: str, description: object = None) -> dict[str, object]:
    """`items` (None when none) and `no_item_why` (None when there are) of the spawn's row."""
    items, why = declared(prompt) or (named(prompt, description), None)
    return {"items": items or None, "no_item_why": None if items else why or NONE}
