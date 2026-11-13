"""goh_root(): the gates_of_heck root, with a GOH_DIR naming a REMOVED HEAD export pointed back at a
live checkout (GOH.TRANSIENTDIR).

The Python half of lib/goh_root.sh's `goh_dir_repair` (read it for the story): a GOH_DIR that is
not a directory -- a pruned `~/.cache/goh/head/<sha>` a long-lived process inherited -- falls back
to GOH_LIVE_ROOT, then the default checkout, with a line on stderr. A GOH_DIR that exists, and the
default when none is set, are returned as they are. Pinned by tests/test_dead_goh_dir.py.
"""

from __future__ import annotations

import os
import sys

DEFAULT = os.path.join("~", "Projects", "gates_of_heck")


def goh_root(env: os._Environ[str] | dict[str, str] | None = None) -> str:
    """GOH_DIR when it is a live directory, else GOH_LIVE_ROOT, else the default checkout."""
    env = os.environ if env is None else env
    default = os.path.expanduser(DEFAULT)
    goh = env.get("GOH_DIR") or default
    if os.path.isdir(goh) or goh == default:
        return goh
    for cand in (env.get("GOH_LIVE_ROOT") or "", default):
        if cand and os.path.isdir(os.path.join(cand, "gates")):
            print(
                f"⚠ gates_of_heck: GOH_DIR={goh} no longer exists (a pruned HEAD export this "
                f"process inherited; restart it) -- using {cand}",
                file=sys.stderr,
            )
            return cand
    return goh
