#!/usr/bin/env python3
"""The backstop: the minute job that kills the spinners whose owner is gone.

    tools/load_reap.py [--dir DIR]

`tools/load.py` reaps itself: it kills the spinners it started on the way out, and it watches for
its own owner dying and does the same then. What it cannot do is run at all when it is SIGKILLed
-- and a SIGKILLed owner is exactly how the three leaks of 2026-10-20 happened. So the machine
runs this: a launchd job (`com.ztomer.goh-load-reap`) every minute, reading the registry the tool
wrote, killing the pids in it whose parent is 1, and dropping the entries that are gone.

It kills by PID, from its own registry, and never by name: a name is not an owner, and the house
forbids killing by name (`goh kill-by-name`). A spinner with a LIVE parent is an experiment in
progress and is left alone; a process no registry names was never ours.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import load_registry as reg  # noqa: E402
import load_reap  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/load_reap.py",
        description="kill the load spinners whose owner is gone",
    )
    parser.add_argument("--dir", default=None, help="the load directory (default GOH_LOAD_DIR)")
    opts = parser.parse_args(argv)
    directory = Path(opts.dir).expanduser() if opts.dir else reg.load_dir()
    if not directory.is_dir():
        return 0  # no experiment has ever run here; nothing to reap
    report = load_reap.reap_all(directory)
    if report.killed or report.removed:
        killed = " ".join(str(pid) for pid in report.killed)
        print(
            f"load-reap: killed {killed or 'nothing'}; removed {len(report.removed)} registry file(s)"
        )
    if report.failed:
        print(
            f"load-reap: {len(report.failed)} spinner(s) survived: "
            f"{' '.join(str(pid) for pid in report.failed)}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
