"""Whether a session's ARGUMENTS select the whole suite: no node, path, -k, -m, --deselect or --lf.

One definition for the two readers that must agree on it: the land's verdict store, which records
a run `full` only when it was whole (tests/_land_verdicts.py), and the file-event ceiling, which
judges a run's mean only when it was whole (tests/_fs_churn.py, GOH.FSCEILINGRED). What a plugin
deselects after collection (a carried run, a shard) is the readers' own count, never seen here.
"""

from __future__ import annotations

from pathlib import Path


def whole(config) -> bool:
    """No node, path below tests/, -k, -m, --deselect or --lf narrowed this session."""
    o = config.option
    if any(getattr(o, k, None) for k in ("keyword", "markexpr", "deselect", "lf")):
        return False
    roots = {Path(config.rootpath), Path(config.rootpath) / "tests"}
    return all("::" not in a and Path(a).resolve() in roots for a in config.args)
