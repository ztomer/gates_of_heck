"""goh_root(): the gates_of_heck root, with a GOH_DIR naming a REMOVED HEAD export pointed back at a
live checkout (GOH.TRANSIENTDIR); and the one rule for GOH_LIVE_ROOT (GOH.LIVEROOTLEAK).

The Python half of lib/goh_root.sh (read it for the story). GOH_LIVE_ROOT is an EXPORT's: it is
honoured only for a directory that is an export -- it holds `.goh-head`, or, pruned, it sat in the
head export cache. A GOH_DIR that is not a directory -- a pruned `~/.cache/goh/head/<sha>` a
long-lived process inherited -- falls back to its live root when it was an export, then the default
checkout, with a line on stderr. A GOH_DIR that exists, and the default when none is set, are
returned as they are. Pinned by tests/test_live_root_rule.py and tests/test_dead_goh_dir.py.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

DEFAULT = os.path.join("~", "Projects", "gates_of_heck")


def head_cache(env: Mapping[str, str] | None = None) -> str:
    """Where gates/_from_head.sh builds every HEAD export (lib/goh_root.sh `goh_head_cache`)."""
    env = os.environ if env is None else env
    if env.get("GOH_HEAD_CACHE"):
        return env["GOH_HEAD_CACHE"]
    cache = env.get("XDG_CACHE_HOME") or os.path.join(env.get("HOME", ""), ".cache")
    return os.path.join(cache, "goh", "head")


def is_export(d: str, env: Mapping[str, str] | None = None) -> bool:
    """`d` is a HEAD export, or was one and has been pruned (lib/goh_root.sh `goh_is_export`)."""
    if os.path.isfile(os.path.join(d, ".goh-head")):
        return True
    return not os.path.lexists(d) and os.path.dirname(d) == head_cache(env)


def live_root_for(d: str, env: Mapping[str, str] | None = None) -> str:
    """GOH_LIVE_ROOT when `d` is an export, else "" (lib/goh_root.sh `goh_live_root_for`)."""
    env = os.environ if env is None else env
    live = env.get("GOH_LIVE_ROOT") or ""
    return live if live and is_export(d, env) else ""


def goh_root(env: Mapping[str, str] | None = None) -> str:
    """GOH_DIR when it is a live directory, else its live root if it was an export, else the
    default checkout."""
    env = os.environ if env is None else env
    default = os.path.expanduser(DEFAULT)
    goh = env.get("GOH_DIR") or default
    if os.path.isdir(goh) or goh == default:
        return goh
    for cand in (live_root_for(goh, env), default):
        if cand and os.path.isdir(os.path.join(cand, "gates")):
            print(
                f"⚠ gates_of_heck: GOH_DIR={goh} no longer exists (a pruned HEAD export this "
                f"process inherited; restart it) -- using {cand}",
                file=sys.stderr,
            )
            return cand
    return goh
