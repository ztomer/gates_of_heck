"""check_lock_order.py's reading of CALLS (GOH.LOCKORDERBIN2): a `goh SUB` call is the house script
SUB runs, and a detached call forms no edge.

`goh land` IS tools/landq.py (crates/goh/house_scripts.tsv): a shell command `goh land` (bare, or
`"$GOH/bin/goh" land`) or a Python argv `[goh, "land", ...]` refers to it; a word in prose or a
tuple of names is neither. A call whose child starts a session of its own -- `--detach`, `setsid`,
`start_new_session=True` -- holds none of the caller's locks, so its references are dropped.

`check_lock_order.py`'s reading of calls (GOH.LOCKORDERBIN2): a `goh SUB` shell command or `[goh,
"SUB", ...]` argv is the house script SUB runs (`crates/goh/house_scripts.tsv`), and a detached
call (`--detach`, `setsid`, `start_new_session=True`) forms no edge: its child holds none of the
caller's locks. Pinned by `test_check_lock_order.py`.

`checks/check_lock_order.py`: The static half of the lock order (GOH.LOCKORDER): every flock in the
tree goes through `lib/lock_order.py`/`.sh` (a raw `fcntl.flock`, or a Rust file lock with no
`lock-order:` mark, is red); `lib/lock_order.tsv` names exactly the files that take each lock; and
the held-while-acquiring graph -- a lock held over the rest of a shell script or of a Python
function (and the same-file functions it calls), every goh script, import, `.gatesrc` key command
and `X ... -- G` wrapper run there -- sorts topologically: a cycle is red, named edge by edge, and
so is an edge against the ranks. A `GOH_CI_STEPS` step. Pinned by `test_check_lock_order.py`
(calibrated: the 2026-11-15 land-inside-the-slot invocation re-planted is red).
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

HOUSE_SCRIPTS = "crates/goh/house_scripts.tsv"
# `goh SUB` as a shell command (`goh land`, `"$GOH/bin/goh" land`), and as a Python argv
# (`[goh, "land"`, `["bin/goh", "land"`): a word in prose or a tuple of names is neither.
SUBCMD_SH = re.compile(
    r"""(?:^|[;&|(`]|\$\()\s*(?:(?:exec|if|then|do|else|command|!)\s+)*"""
    r"""["']?(?:[^\s"';&|]*/)?goh["']?\s+([a-z][a-z0-9-]*)\b"""
)
SUBCMD_PY = re.compile(r"""\[\s*(?:goh|["'](?:[^"']*/)?goh["'])\s*,\s*["']([a-z][a-z0-9-]*)["']""")
DETACH_SH = re.compile(r"(?:^|\s)--detach\b|\bsetsid\b")


def house_subcommands(root: Path, tracked: set[str]) -> dict[str, list[str]]:
    """`goh SUB` (each alias too) -> the tracked house script it runs."""
    try:
        rows = (root / HOUSE_SCRIPTS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, list[str]] = {}
    for row in rows:
        cols = row.split("\t")
        if row.startswith("#") or len(cols) < 2 or cols[1] not in tracked:
            continue
        for sub in cols[0].split("|"):
            out.setdefault(f"goh {sub}", []).append(cols[1])
    return out


def subcommands(rel: str, text: str, names: dict[str, list[str]]) -> set[str]:
    """The house scripts the `goh SUB` calls in TEXT run (a shell's commands, a Python argv)."""
    rx = SUBCMD_SH if rel.endswith(".sh") else SUBCMD_PY if rel.endswith(".py") else None
    hit: set[str] = set()
    for sub in rx.findall(text) if rx else ():
        hit.update(names.get(f"goh {sub}", ()))
    return hit


def detached(rel: str, lines: list[str], uncomment: Callable[[str, str], str]) -> set[int]:
    """The lines of a call whose child runs in a session of its own: a shell line naming
    `--detach` or `setsid`; every line of a Python call passing `start_new_session=True` or a
    `--detach`/`setsid` word."""
    if not rel.endswith(".py"):
        return {n for n, line in enumerate(lines, 1) if DETACH_SH.search(uncomment(rel, line))}
    try:
        parsed = ast.parse("\n".join(lines))
    except SyntaxError:
        return set()
    out: set[int] = set()
    for node in ast.walk(parsed):
        if not isinstance(node, ast.Call):
            continue
        new_session = any(k.arg == "start_new_session" and isinstance(k.value, ast.Constant)
                          and k.value.value is True for k in node.keywords)  # fmt: skip
        words = {c.value for c in ast.walk(node) if isinstance(c, ast.Constant)}
        if new_session or {"--detach", "setsid"} & words:
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out
