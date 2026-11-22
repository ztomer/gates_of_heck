"""THE PRE-STEP'S CPU (GOH.PREWIDTH): user+system seconds of its pytest process tree.

    counted()                   # tools/affected_tests.py: around each pytest run it reaps
    tree_cpu(root, snapshot)    # lib/pre_deadline.py, at the deadline: the pytest runs still live
    read(path)                  # tools/land.sh, for the land record's `pre_run.cpu_s`

The land record kept a pre-step's workers and wall seconds but not what the set cost the machine,
so "8 workers for 400 s" could not be told from "8 workers, 3 of them busy". The file is named by
GOH_LAND_PRE_CPU (tools/land.sh: beside the pre-step's start mark). A finished pytest run is counted
by its reaper (RUSAGE_CHILDREN around the run: the run's whole tree, its xdist workers included); a
run killed at the deadline has no reaper that lives to count it, so the deadline wrapper reads the
live tree first. Pinned by tests/test_pre_width.py.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

ENV = "GOH_LAND_PRE_CPU"


def read(path: Path | str) -> float | None:
    """The seconds the file holds; None when there is none (no pytest ran, or it is unreadable)."""
    try:
        return float(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def add(path: Path | str, seconds: float) -> None:
    """Add SECONDS to the file (one writer at a time: the pre-step's runs are sequential, and the
    deadline wrapper writes while the run it counts is still blocked in its pytest, just before the
    kill). A failed write is dropped: the instrument is never why a pre-step goes red."""
    total = (read(path) or 0.0) + max(0.0, seconds)
    with contextlib.suppress(OSError):
        tmp = f"{path}.{os.getpid()}"
        Path(tmp).write_text(f"{total:.3f}\n", encoding="utf-8")
        os.replace(tmp, path)


def reaped() -> float:
    """User+system seconds of every child this process has reaped, theirs included."""
    import resource

    u = resource.getrusage(resource.RUSAGE_CHILDREN)
    return u.ru_utime + u.ru_stime


@contextlib.contextmanager
def counted(environ: dict[str, str] | None = None) -> Iterator[None]:
    """Add what the children reaped inside the block cost to GOH_LAND_PRE_CPU's file, if named."""
    path = (os.environ if environ is None else environ).get(ENV, "")
    before = reaped()
    try:
        yield
    finally:
        if path:
            add(path, reaped() - before)


def tree_cpu(root: int, snapshot: dict) -> float:
    """CPU of every pytest tree under ROOT in SNAPSHOT (lib/proc_table.py `Proc`s): each live
    member's own seconds plus its reaped children's. A live child is counted on its own, never in
    its parent's reaped total, so nothing counts twice."""
    kids: dict[int, list[int]] = {}
    for p in snapshot.values():
        kids.setdefault(p.ppid, []).append(p.pid)

    def below(pid: int) -> list[int]:
        out, todo = [], list(kids.get(pid, []))
        while todo:
            n = todo.pop()
            out.append(n)
            todo.extend(kids.get(n, []))
        return out

    under = [root, *below(root)] if root in snapshot else below(root)
    tops = [n for n in under if snapshot[n].script == "pytest"]
    members = set(tops)
    for top in tops:
        members.update(below(top))
    return sum(snapshot[n].cpu_s + (snapshot[n].child_s or 0.0) for n in members)


def at_deadline(root: int, path: Path | str | None) -> None:
    """Before the deadline kills ROOT's tree: add its live pytest runs' CPU to PATH."""
    if not path:
        return
    with contextlib.suppress(Exception):  # an instrument: never the reason the kill is late
        import proc_table

        add(path, tree_cpu(root, proc_table.reader().snapshot()))
