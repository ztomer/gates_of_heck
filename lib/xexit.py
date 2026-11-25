#!/usr/bin/env python3
"""A step's words and its exit code must agree: a `✗` status line at exit 0 is a red step (GOH.XEXIT).

usage: xexit.py [--say LABEL [--keep]] LOG   prints each `✗` status line in LOG; exit 1 if any.
  --say LABEL: to stderr, with the refusal naming LABEL (what goh_step and local_ci.sh print): each
    `✗` line with the indented detail under it (at most DETAIL_LINES each) and the full log's path.
  --keep: name a copy of LOG beside it instead, for a caller (goh_step) that deletes LOG at exit.

In the house TUI contract `✗` means FAILURE and `⚠` means advisory. A private Swift app's
census_bench void hold and three render selftests printed `✗` and exited 0, and the gate called them
green (fixed there in a private Swift app 06ee510e). goh_step and local_ci.sh run every gate step,
so both ask this ONE predicate after a step exits 0; the Rust tier's delegated steps ask its twin
(crates/goh/src/step_report.rs `cross_lines`). A status line is one whose first visible
character, after indentation and colour escapes, is `✗`; a `✗` quoted mid-line (a calibration
plant's row, a docs table) is text, not a verdict. An advisory says `⚠`.

`goh_step` and `local_ci.sh` refuse a step that printed a `✗` status line (first visible character,
after indentation and colour) and exited 0 -- a private Swift app's census_bench void hold and
three render selftests did (GOH.XEXIT). An advisory says `⚠`; a `✗` quoted mid-line is text.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import re
import sys
from pathlib import Path

CROSS = "✗"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


_LINE_BREAK = re.compile(r"\r\n?|\n")
DETAIL_LINES = 20


def failure_lines(text: str) -> list[str]:
    # A line ends at `\n` or `\r` and nowhere else (GOH.XEXIT2): a terminal shows the text after a
    # `\r`, so a progress line's last segment is its own status line. Not `str.splitlines()`, which
    # also breaks on VT, FF, U+2028 and friends that the Rust twin does not; tests/fixtures/xexit
    # pins the twins to one answer.
    out = []
    for raw in _LINE_BREAK.split(text):
        line = _ANSI.sub("", raw).strip()
        if line.startswith(CROSS):
            out.append(line)
    return out


def failure_blocks(text: str, per: int = DETAIL_LINES) -> list[str]:
    # What the refusal SHOWS (GOH.XEXITDETAIL): each `✗` status line and the lines under it that are
    # indented deeper, up to `per` of them, the rest counted. A header such as `✗ compiler warnings
    # in our sources:` is nothing without its list: a private Swift app's push (2026-11-16) showed
    # it bare, which hid that the file it named was a selftest's planted fixture. A blank line, a
    # line at or left of the `✗`, or another `✗` status line ends the block. The verdict stays
    # `failure_lines`.
    lines = [_ANSI.sub("", raw).rstrip() for raw in _LINE_BREAK.split(text)]
    out: list[str] = []
    i = 0
    while i < len(lines):
        body = lines[i].lstrip()
        i += 1
        if not body.startswith(CROSS):
            continue
        depth = len(lines[i - 1]) - len(body)
        out.append(body)
        shown = more = 0
        while i < len(lines):
            nxt = lines[i]
            nb = nxt.lstrip()
            if not nb or len(nxt) - len(nb) <= depth or nb.startswith(CROSS):
                break
            if shown < per:
                out.append(nxt[depth:])
                shown += 1
            else:
                more += 1
            i += 1
        if more:
            out.append(
                f"  → {more} more {'line' if more == 1 else 'lines'} not shown (full log below)"
            )
    return out


def _kept_copy(log: Path) -> Path:
    import shutil
    import tempfile

    fd, name = tempfile.mkstemp(prefix="goh-xexit-", suffix=".log", dir=log.parent)
    with open(fd, "wb") as dst, log.open("rb") as src:
        shutil.copyfileobj(src, dst)
    return Path(name)


def main(argv: list[str]) -> int:
    args = argv[1:]
    label = None
    if args[:1] == ["--say"] and len(args) >= 2:
        label, args = args[1], args[2:]
    keep = label is not None and args[:1] == ["--keep"]
    if keep:
        args = args[1:]
    if len(args) != 1:
        sys.stderr.write("usage: xexit.py [--say LABEL [--keep]] LOG\n")
        return 2
    log = Path(args[0])
    text = log.read_text(encoding="utf-8", errors="replace")
    hits = failure_lines(text)
    if not hits:
        return 0
    if label is None:
        print("\n".join(hits))
        return 1
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent)
    )  # tui.lib, whatever the caller's path
    from tui.lib import counted, err

    sys.stderr.write("\n" + "\n".join(failure_blocks(text)) + "\n\n")
    err(
        f"{label} printed {counted(len(hits), '✗ line')} above and exited 0"
        " -- a ✗ is a failure in the house"
    )
    err("  contract: exit nonzero, or say ⚠ if it is an advisory (GOH.XEXIT, lib/xexit.py)")
    try:
        named = _kept_copy(log) if keep else log
    except OSError as e:
        err(f"  full log: not kept ({e})")
    else:
        err(f"  full log: {named}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
