#!/usr/bin/env python3
"""A step's words and its exit code must agree: a `✗` status line at exit 0 is a red step (GOH.XEXIT).

usage: xexit.py [--say LABEL] LOG   prints each `✗` status line in LOG; exit 1 when there is one.
  --say LABEL: to stderr, with the refusal naming LABEL (what goh_step and local_ci.sh print).

In the house TUI contract `✗` means FAILURE and `⚠` means advisory. ZoneWM's census_bench void
hold and three render selftests printed `✗` and exited 0, and the gate called them green (fixed
there in ZoneWM 06ee510e). goh_step and local_ci.sh run every gate step, so both ask this ONE
predicate after a step exits 0; the Rust tier's delegated steps ask its twin
(crates/goh/src/step_report.rs `cross_lines`). A status line is one whose first visible
character, after indentation and colour escapes, is `✗`; a `✗` quoted mid-line (a calibration
plant's row, a docs table) is text, not a verdict. An advisory says `⚠`.
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


def failure_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        if line.startswith(CROSS):
            out.append(line)
    return out


def main(argv: list[str]) -> int:
    args = argv[1:]
    label = None
    if args[:1] == ["--say"] and len(args) >= 2:
        label, args = args[1], args[2:]
    if len(args) != 1:
        sys.stderr.write("usage: xexit.py [--say LABEL] LOG\n")
        return 2
    hits = failure_lines(Path(args[0]).read_text(encoding="utf-8", errors="replace"))
    if not hits:
        return 0
    if label is None:
        print("\n".join(hits))
        return 1
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent)
    )  # tui.lib, whatever the caller's path
    from tui.lib import err

    sys.stderr.write("\n" + "\n".join(hits) + "\n\n")
    err(f"{label} printed the ✗ line(s) above and exited 0 -- a ✗ is a failure in the house")
    err("  contract: exit nonzero, or say ⚠ if it is an advisory (GOH.XEXIT, lib/xexit.py)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
