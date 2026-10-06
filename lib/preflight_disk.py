#!/usr/bin/env python3
"""preflight_disk.py <path> <need-GiB> -- a cold gate needs room, and a full disk fails AS a full disk.

ZoneWM, 2026-10-06: a pre-push gate failed twice in its sanitizer stage with `CodeSign ... failed`
and "could not write dependency graph"; the volume had 10 GiB free and the cold build needs 15. The
red named the sanitizer, so the first reading was a race. `push_gate.sh` runs this before it makes
the export (`GOH_MIN_FREE_GIB`), and `gates/round.sh` before it commits: the refusal names the free
space and the need instead. Exit 1 short, 0 with room, 2 for a usage error.

A preflight, never a `check_*` gate: its subject is the machine, not the tree.
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

import shutil
import sys

GIB = 1024**3


def shortfall(free_bytes: int, need_gib: float) -> str | None:
    """Why a cold gate cannot run with this much free, or None when it can."""
    free_gib = free_bytes / GIB
    if free_gib >= need_gib:
        return None
    return (
        f"{free_gib:.1f} GiB free on the volume a cold gate builds on, and it needs {need_gib:g} GiB "
        "(GOH_MIN_FREE_GIB): free space first, or the gate fails later as an error that is not one "
        "(a codesign, a sanitizer, a linker)"
    )


def main(argv: list[str]) -> int:
    try:
        path, need = argv[0], float(argv[1])
    except (IndexError, ValueError):
        print("usage: preflight_disk.py <path> <need-GiB>", file=sys.stderr)
        return 2
    why = shortfall(shutil.disk_usage(path).free, need)
    if why:
        print(f"✗ {why}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
