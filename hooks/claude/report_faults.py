"""The `Faults seen:` section of an agent's report, read as a list (agent_guard's outcome line).

Split out of agent_guard.py (its 500-line cap) when the section grew bullets (LOOP.FAULTBULLETS).

`read_faults()`: a subagent report's `Faults seen:` section as a list, the line's top-level `;`
faults plus each `- `/`* `/`1. ` bullet under it; an empty line with no list is not stated (None)
with a warning `agent_guard.py` passes to the session (LOOP.FAULTBULLETS). Pinned by
`tests/test_agent_guard_faults.py`.
"""

from __future__ import annotations

import re

FAULTS_LINE = re.compile(
    r"^[ \t>*-]*Faults[ \t]+seen:[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE
)
NO_FAULTS = {"none", "none.", "-", "n/a"}

_OPENERS = {"(": ")", "[": "]"}


def _split_top_level(body: str) -> list[str]:
    """`body` split on `;` outside (), [], double and single quotes, backticks (GOH.FAULTSPLIT).

    A fault is one claim: "(only in my side run; the land gate passed)" is not two. A single quote
    opens or closes a span only at a word edge, so an apostrophe ("doesn't") is text. A line that
    ends with a span still open (an agent dropped a closer) falls back to the plain split: a stray
    opener must never merge faults or lose one. Text the ledger already holds is never re-split
    (rows carry their `faults` list), so existing fault keys are untouched.
    """
    parts: list[str] = []
    start = 0
    stack: list[str] = []  # the closers awaited, innermost last
    quote = ""  # the quote char of the open span, else ""
    for i, ch in enumerate(body):
        if quote:
            if ch == quote and (ch != "'" or not body[i + 1 : i + 2].isalnum()):
                quote = ""
        elif ch in '"`' or (ch == "'" and not body[i - 1 : i].isalnum()):
            quote = ch
        elif ch in _OPENERS:
            stack.append(_OPENERS[ch])
        elif stack and ch == stack[-1]:
            stack.pop()
        elif ch == ";" and not stack:
            parts.append(body[start:i])
            start = i + 1
    if quote or stack:
        return body.split(";")
    parts.append(body[start:])
    return parts


BULLET = re.compile(r"^[ \t]*(?:[-*\u2022]|\d+[.)])[ \t]+(.*?)[ \t]*$")


def read_faults(report: str) -> tuple[list[str] | None, str | None]:
    """(the faults, a warning): `faults`, and why the section could not be read when it could not.

    A fault is the text on the `Faults seen:` line, split on top-level `;`, AND each bullet (`- `,
    `* `, `1. `) in the block under it, until a blank line, an unindented non-bullet line or the
    end; an indented line under a bullet continues it. Read from the line alone, a report that
    listed its faults as bullets under an empty line was recorded as NO faults -- four vanished
    from EST.STORE1116's (LOOP.FAULTBULLETS, 2026-11-13). An empty line with no bullets under it
    says neither none nor a fault: not stated (None), with a warning."""
    m = FAULTS_LINE.search(report)
    if m is None:
        return None, None
    body = m.group(1).strip()
    bullets: list[str] = []
    lines = report[m.end() :].splitlines()[1:]
    # A blank line between a bare heading and its list.
    while not body and lines and not lines[0].strip():
        lines.pop(0)
    for line in lines:
        b = BULLET.match(line)
        if b and b.group(1):
            bullets.append(b.group(1))
        elif bullets and line.strip() and line[:1] in " \t":
            bullets[-1] = f"{bullets[-1]} {line.strip()}"
        else:
            break
    # Backticks are looked through ONLY to recognise a wrapped `none`; a fault is recorded as
    # written, so a leading backtick on the first one is part of it.
    said_none = body.strip("`").strip().lower() in NO_FAULTS
    found = [] if said_none or not body else [f.strip() for f in _split_top_level(body)]
    found = [f for f in found if f] + bullets
    if found or said_none:
        return found, None
    return None, (
        "agent_guard: the report's `Faults seen:` line is empty and no bullet list follows it, so "
        "its faults were recorded as not stated -- write `Faults seen: none`, the faults on the "
        "line `;`-separated, or one `- ` bullet per fault directly under it"
    )


def faults(report: str) -> list[str] | None:
    """The report's faults as a list (`read_faults`): [] for none, None when not stated (the line
    absent, or empty with nothing under it), which is not the same claim as none."""
    return read_faults(report)[0]
