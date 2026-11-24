"""Whether a session's ARGUMENTS select the whole suite: no node, path, -k, -m, --deselect or --lf.

One definition for the two readers that must agree on it: the land's verdict store, which records
a run `full` only when it was whole (tests/_land_verdicts.py), and the file-event ceiling, which
judges a run's mean only when it was whole (tests/_fs_churn.py, GOH.FSCEILINGRED). What a plugin
deselects after collection (a carried run, a shard) is the readers' own count, never seen here.

`unrun` is the other side of the arguments (GOH.CARRYEXIT5): the node ids a session was HANDED that
no report and no carry accounts for. A carried run whose pytest said "no tests ran" is green only
when there are none (tests/_land_verdicts.py): GOH.SHAREZERO's group ran none of its ids and passed.
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


def unrun(config, accounted: set[str]) -> list[str]:
    """The node ids among this session's arguments that no id in ACCOUNTED (run or carried) is or
    parametrizes, each said as an `ERROR` line a land's red reads (lib/land_cause.py)."""
    root, here = Path(config.rootpath).resolve(), Path(config.invocation_params.dir)
    lost = []
    for arg in config.args:
        path, sep, rest = arg.partition("::")
        if not sep:
            continue
        full = (here / path).resolve()
        node = f"{full.relative_to(root).as_posix() if full.is_relative_to(root) else path}::{rest}"
        if not any(n == node or n.startswith((f"{node}[", f"{node}::")) for n in accounted):
            lost.append(node)
    term = config.pluginmanager.get_plugin("terminalreporter")
    for node in lost:
        line = f"ERROR {node} - handed to this run, neither run nor carried (GOH.CARRYEXIT5)"
        (term.write_line if term else print)(line)
    return lost
