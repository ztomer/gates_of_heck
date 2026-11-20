#!/usr/bin/env python3
"""fs_events.py -- the file events CMD makes under each --root, and who made them.

    tools/fs_events.py run --root "$TMPDIR/goh.noindex" --root ~/.cache/goh -- tools/pytest.sh
    tools/fs_events.py run --json out.json --root DIR -- CMD...

It measures; the suite's own count and ceiling are tests/_fs_churn.py's (GOH.FSCHURN2). Exit: CMD's,
or 3 where FSEvents is unavailable. Pinned by tests/test_fs_churn.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import fs_events
from fs_events import UNAVAILABLE, measure


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=fs_events.__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run", help="count the events under --root while CMD runs")
    run.add_argument("--root", action="append", required=True)
    run.add_argument("--json", help="write the report here too")
    run.add_argument("--top", type=int, default=10)
    run.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = a.cmd[1:] if a.cmd[:1] == ["--"] else a.cmd
    if not cmd:
        ap.error("no command after --")
    try:
        rc, rep = measure(a.root, cmd, a.top)
    except OSError as e:
        print(f"fs_events: unavailable: {e}", file=sys.stderr)
        return UNAVAILABLE
    text = json.dumps(rep, indent=1)
    if a.json:
        Path(a.json).write_text(text + "\n")
    print(text, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
