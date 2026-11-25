"""What each test file READ, as a verdict record keeps it, and the one join of them
(GOH.READUNION).

A record's `reads` ({test file: the paths its tests read}) is what a carry asks before it takes a
pass across a diff (tests/_read_audit.py): a list that is too short carries a test past a change to
a file it reads, a wrong green. One record is made of many runs -- a pre-step's slice, a gate's
carried siblings, an enqueue's owed tests, the Linux worker's share -- and each place that joined
them did it its own way: the FIRST list won in the carry (`setdefault`), the LAST in the judges'
merge and in the suite-once record, and a run that watched nothing (the worker's) took a file's old
list as its own. Over the 80 suite records of 2026-11-24..25, 29 dropped reads of nodes they
carried (265 file lists, 795 paths) and 15 carried 259 node verdicts across a change to a dropped
path. So there is one join:

    joined(passed, parts)   {file: reads} for PASSED, of PARTS: (nodes, reads) pairs, each the nodes
                            ONE run or record answers for and what it saw their files read. Lists
                            are united. A file is named only when EVERY passed node of it is in a
                            part that watched its file: one unwatched node leaves the file out, and
                            a file left out is refused by every carry (its tests run)
    of(rec)                 a record's reads, or {} when it is no VERSION record: one written before
                            this module may hold a truncated list, so none of it is believed
    stamped(passed, parts)  the two keys a record keeps: `reads` and `reads_v`

The join is per file, not per node, on purpose: a module's fixture reads for whichever of its tests
ran first. Pinned by tests/test_read_sets.py; tests/test_read_sets_one_join.py refuses a second
join.

GOH.READUNION: the one join of what test files read (`joined`: united, and a file any of whose
passed nodes nobody watched is left out, so a carry refuses it), and the record version that keeps
an older, truncated list from being believed (`of`, `stamped`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

KEY, VERSION_KEY = "reads", "reads_v"
VERSION = 2  # 1 (no key): joined by whichever list came first or last, so possibly truncated
REMOTE = "remote_reads"  # a land record's: what the Linux worker's passes read (lib/remote_lane.py)
SPAN = 64  # more paths than this of one file are kept as their directories

Reads = Mapping[str, Iterable[str]]
Part = tuple[Iterable[str], Reads]


def file_of(node: str) -> str:
    return node.split("::")[0]


def kept(paths: Iterable[str]) -> list[str]:
    """PATHS as stored: each path, or past SPAN their directories, two levels deep: coarser, so a
    carry refuses more, never less."""
    paths = set(paths)
    if len(paths) <= SPAN:
        return sorted(paths)
    dirs = set()
    for p in paths:
        parts = p.rstrip("/").split("/")
        dirs.add("/".join(parts[: min(2, len(parts) - 1)]) + "/" if len(parts) > 1 else p)
    return sorted(dirs)


def joined(passed: Iterable[str], parts: Iterable[Part]) -> dict[str, list[str]]:
    """{test file: what it read} for the files of PASSED every node of which some part watched
    (module doc); the lists of every part that answers for a passed node of the file, united."""
    want = set(passed)
    watched: set[str] = set()
    out: dict[str, set[str]] = {}
    for nodes, reads in parts:
        mine = {n for n in want.intersection(nodes) if file_of(n) in reads}
        watched |= mine
        for f in {file_of(n) for n in mine}:
            out.setdefault(f, set()).update(reads[f])
    blind = {file_of(n) for n in want - watched}
    return {f: sorted(out[f]) for f in sorted(out) if f not in blind}


def of(rec: object, key: str = KEY) -> dict[str, list[str]]:
    """REC's reads under KEY; {} for no record, none kept, or a record of another VERSION."""
    if not isinstance(rec, dict) or rec.get(VERSION_KEY) != VERSION:
        return {}
    reads = rec.get(key)
    return reads if isinstance(reads, dict) else {}


def part(rec: object, key: str = KEY, nodes: str = "passed") -> Part:
    """What REC answers for: its NODES (`passed`, or a land record's `remote`) and their reads."""
    got = rec.get(nodes) if isinstance(rec, dict) else None
    return (got or ()), of(rec, key)


def stamped(passed: Iterable[str], parts: Iterable[Part]) -> dict[str, object]:
    return {KEY: joined(passed, parts), VERSION_KEY: VERSION}
