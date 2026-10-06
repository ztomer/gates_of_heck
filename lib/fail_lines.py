#!/usr/bin/env python3
"""The failure block goh_step prints before a red step's tail: which lines name the failure.

usage: fail_lines.py LOG   (GOH_FAIL_PATTERN, GOH_FAIL_LINES as in docs/config.md)
Prints the block, or nothing when there is nothing to name.

It used to be one grep over the whole log, and the whole log of an outer step is every sub-step's
output: a red `make verify-clean` listed a calibration plant's quoted `✗` row as its failure,
though the plant had exited 0 and the `✗` was the point of it (ZoneWM H2, BACKLOG C5). A line
names the failure only when it can be ATTRIBUTED to the sub-step that failed, so:

* NESTED house steps. An inner goh_step that failed already printed its own block, from its own
  log. That block is the attribution: quote the innermost one alone, name its step, and COUNT the
  matching lines left out -- they came from sub-steps that exited 0, and the tail still has them.
* An OPAQUE command that ran many sub-steps (one `make` over many recipes) frames none of them, so
  nothing in its log can be attributed. Say so rather than assert it, and put first the failing
  sub-step the command named itself (make's `*** [target] Error N`).
* A plain command: the grep it always was.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HEADER = "── failure lines"
TAIL = "── tail ──"
DEFAULT_PATTERN = r"error:|FAILED|failed|panicked at|Assertion|✗"
# Summary lines that contain the pattern's words while reporting success.
NOT_A_FAILURE = re.compile(r"0 failures|passed|failures \(0")
DIE = re.compile(r"^✗ .*?: (?P<label>.+) failed \(command: ")
MAKE_ERROR = re.compile(r"^g?make(\[\d+\])?: \*\*\* ")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
# This module's own notes, which an enclosing step's log carries and must not count as failures.
LEFT_OUT = "· left out:"
MADE = "· the failing sub-step, as make named it:"
WHOLE_LOG = "· the matches below are from the WHOLE log:"
NOTES = (LEFT_OUT, MADE, WHOLE_LOG)


def _matches(lines: list[str], pattern: re.Pattern[str]) -> list[int]:
    return [
        i for i, line in enumerate(lines) if pattern.search(line) and not NOT_A_FAILURE.search(line)
    ]


def _dumps(lines: list[str]) -> list[tuple[int, int, int]]:
    """(start, header, die) of every failure dump a sub-step printed, in log order.

    A dump runs from the TIMED OUT lines goh_step prints before its header to the die line naming
    the step. The LAST one is the innermost: an enclosing step's dump carries the inner one in its
    tail, so the innermost is always printed after every dump that encloses it.
    """
    out = []
    for h in (i for i, line in enumerate(lines) if line.startswith(HEADER)):
        die = next((i for i in range(h + 1, len(lines)) if DIE.match(lines[i])), len(lines) - 1)
        start = h
        while start > 0 and lines[start - 1].strip():
            start -= 1
        out.append((start, h, die))
    return out


def block(text: str, pattern_src: str = DEFAULT_PATTERN, cap: int = 40) -> str:
    lines = [ANSI.sub("", line) for line in text.splitlines()]
    pattern = re.compile(pattern_src)
    out: list[str] = []
    dumps = _dumps(lines)
    if dumps:
        start, h, die = dumps[-1]
        tail = next((i for i in range(h + 1, die) if lines[i] == TAIL), die)
        named = DIE.match(lines[die])
        label = named.group("label") if named else "a step that printed no name"
        out.append(f"{HEADER}, from the innermost step that failed: {label} ──")
        out += lines[start:h] + lines[h + 1 : tail]
        if named:
            out.append(lines[die])
        # A match inside no dump, and not one of the chain's own die lines or notes, came from a
        # sub-step that passed: counted, never quoted as the failure.
        others = [
            i
            for i in _matches(lines, pattern)
            if not any(a <= i <= b for a, _, b in dumps)
            and not DIE.match(lines[i])
            and not lines[i].startswith(NOTES)
        ]
        if others:
            s = "" if len(others) == 1 else "s"
            out.append(
                f"{LEFT_OUT} {len(others)} other matching line{s}: printed by sub-steps that exited 0"
                " (a calibration plant's quoted row is one) -- the tail below has them"
            )
        out += [
            lines[i]
            for i in range(len(lines))
            if MAKE_ERROR.match(lines[i]) and not start <= i <= die
        ]
        return "\n".join(out) + "\n"
    hits = _matches(lines, pattern)[:cap]
    made = [line for line in lines if MAKE_ERROR.match(line)]
    if not hits and not made:
        return ""
    out.append(f"{HEADER} (grep {pattern_src}) ──")
    if made:
        out.append(MADE)
        out += made
        if hits:
            out.append(
                f"{WHOLE_LOG} make frames no recipe, so one that exited 0 can print them"
                " (a calibration plant's quoted row) -- read them against the target above"
            )
    out += [f"{i + 1}:{lines[i]}" for i in hits]
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    text = Path(argv[1]).read_text(errors="replace")
    pattern = os.environ.get("GOH_FAIL_PATTERN") or DEFAULT_PATTERN
    sys.stdout.write(block(text, pattern, int(os.environ.get("GOH_FAIL_LINES") or 40)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
