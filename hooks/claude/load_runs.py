"""The live load runs, for the hooks that tell a session about the machine (GOH.LOADWHY).

`goh agent-stalls` and the SessionStart advisories (`estate_advisories.py`) both name every live
spinner run with its why and its owner's directory; the reading is lib/load_live.py's. This is the
hooks' one door to it: a hook never fails for what it cannot read, so a missing lib, an unreadable
registry or a bad record says nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path


def lines() -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
    try:
        import load_live

        return load_live.lines()
    except (ImportError, OSError, ValueError, RuntimeError):  # RuntimeError: InWorkTree
        return []
