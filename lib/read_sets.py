"""What each test file READ, as a verdict record keeps it, and the one join of them
(GOH.READUNION).

A record's `reads` (what each test file and test read, in lib/read_tokens.py's words) is what a
carry asks before it takes a pass across a diff (tests/_read_audit.py): a list that is too short
carries a test past a change to a file it reads, a wrong green. A record is made of many runs: a
pre-step's slice, a gate's carried siblings, an enqueue's owed tests, the Linux worker's share. Each
place that joined them did it its own way: the FIRST list won in the carry (`setdefault`), the LAST
in the judges' merge and in the suite-once record, and a run that watched nothing (the worker's)
took a file's old list as its own. Over the 80 suite records of 2026-11-24..25, 29 dropped reads of
nodes they carried (265 file lists, 795 paths) and 15 carried 259 node verdicts across a change to a
dropped path. So there is one join:

    joined(passed, parts)   {file or node: reads} for PASSED, of PARTS: (nodes, reads) pairs, each
                            the nodes ONE run or record answers for and what it saw them read. Lists
                            are united. A file is named only when EVERY passed node of it is in a
                            part that watched its file: one unwatched node leaves the file out, and
                            a file left out is refused by every carry (its tests run)
    heard(cases)            one run's reads, of what each node's report said
    words(node, reads)      what a node read: its file's words and its own
    of(rec)                 a record's reads, or {} when it is no VERSION record: one written before
                            this module may hold a truncated list, so none of it is believed
    stamped(passed, parts)  the two keys a record keeps: `reads` and `reads_v`

A read is its NODE's when nothing but that node could have answered by it, and its FILE's when what
it read may be kept for the next test: made in a wider fixture, an import, or below a module that
keeps state of its own (tests/_read_audit.py `_tell`). Kept per file alone, one test of a file that
lists the tree refused its 26 siblings (GOH.LANDREADS, 18 lands replayed: 94 refused at p50 by file,
28 by node). Pinned by tests/test_read_sets.py; tests/test_read_sets_one_join.py refuses a
second join.

GOH.READUNION: the one join of what test files read (`joined`: united, and a file any of whose
passed nodes nobody watched is left out, so a carry refuses it), and the record version that keeps
an older, truncated list from being believed (`of`, `stamped`).
"""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Iterable, Mapping

KEY, VERSION_KEY = "reads", "reads_v"
# 1 (no key): joined by whichever list came first or last, so possibly truncated. 2: paths alone, a
# module read from its bytecode left out and a listing the prefix of all under it. 3: the words of
# lib/read_tokens.py (GOH.LANDREADS), which a reader of 2 would take for paths.
VERSION = 3
REMOTE = "remote_reads"  # a land record's: what the Linux worker's passes read (lib/remote_lane.py)
SPAN = 64  # a record of more words than this is stored compressed
FILE = "^"  # before a word a run says of a node: its file's (tests/_read_audit.py `_tell`)

Reads = Mapping[str, Iterable[str]]
Part = tuple[Iterable[str], Reads]


def file_of(node: str) -> str:
    return node.split("::")[0]


def heard(cases: Iterable[tuple[str, Iterable[Iterable[str]]]]) -> dict[str, list[str]]:
    """The reads of one run, of (node, the word lists its reports SAID): a word marked FILE under
    its file's key, the rest under the node's. A file with a node that said none was not watched,
    and is left out."""
    out: dict[str, set[str]] = {}
    blind = set()
    for node, said in cases:
        f, lists = file_of(node), list(said)
        out.setdefault(f, set())
        if not lists:
            blind.add(f)
        for word in (w for words in lists for w in words):
            key, w = (f, word[len(FILE) :]) if word.startswith(FILE) else (node, word)
            out.setdefault(key, set()).add(w)
    return {k: sorted(v) for k, v in out.items() if file_of(k) not in blind}


def words(node: str, reads: Reads) -> Iterable[str] | None:
    """What NODE read: its file's words and its own; None when its file was not watched."""
    f = reads.get(file_of(node))
    return None if f is None else [*f, *reads.get(node, ())]


def joined(passed: Iterable[str], parts: Iterable[Part]) -> dict[str, list[str]]:
    """{test file or node: what it read} for the files of PASSED every node of which some part
    watched (module doc); the lists of every part that answers for a passed node, united: a file's
    of each part that ran a node of it, a node's of each that ran the node."""
    want = set(passed)
    watched: set[str] = set()
    out: dict[str, set[str]] = {}
    for nodes, reads in parts:
        mine = {n for n in want.intersection(nodes) if file_of(n) in reads}
        watched |= mine
        for key in {file_of(n) for n in mine} | (mine & reads.keys()):
            out.setdefault(key, set()).update(reads[key])
    for key in out.keys() & want:  # a node's: what its file's words do not say already
        out[key] -= out[file_of(key)]
    blind = {file_of(n) for n in want - watched}
    return {k: sorted(v) for k, v in sorted(out.items())
            if (f := file_of(k)) not in blind and (v or f == k)}  # fmt: skip


def _packed(reads: dict[str, list[str]]) -> dict[str, list[str]] | str:
    """READS as a record stores them: past SPAN words one compressed text, each word and each list
    in it once (a whole-tree copy's 1,700 paths are read by many nodes alike; 102 files' reads of
    2.7 MB are 76 kB so), never fewer words."""
    if sum(map(len, reads.values())) <= SPAN:
        return reads
    lists = list(dict.fromkeys(tuple(v) for v in reads.values()))
    words = sorted({w for v in lists for w in v})
    at, ix = {v: i for i, v in enumerate(lists)}, {w: i for i, w in enumerate(words)}
    text = json.dumps({"words": words, "lists": [[ix[w] for w in v] for v in lists],
                       "of": {k: at[tuple(v)] for k, v in reads.items()}})  # fmt: skip
    return base64.b85encode(zlib.compress(text.encode(), 9)).decode()


def _unpacked(got: object) -> dict[str, list[str]]:
    """What `_packed` was given; {} of anything else."""
    if isinstance(got, str):
        try:
            rec = json.loads(zlib.decompress(base64.b85decode(got)))
            lists = [[rec["words"][i] for i in v] for v in rec["lists"]]
            return {k: lists[i] for k, i in rec["of"].items()}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, zlib.error):
            return {}
    return got if isinstance(got, dict) else {}


def of(rec: object, key: str = KEY) -> dict[str, list[str]]:
    """REC's reads under KEY; {} for no record, none kept, or a record of another VERSION."""
    if not isinstance(rec, dict) or rec.get(VERSION_KEY) != VERSION:
        return {}
    return _unpacked(rec.get(key))


def part(rec: object, key: str = KEY, nodes: str = "passed") -> Part:
    """What REC answers for: its NODES (`passed`, or a land record's `remote`) and their reads."""
    got = rec.get(nodes) if isinstance(rec, dict) else None
    return (got or ()), of(rec, key)


def stamped(passed: Iterable[str], parts: Iterable[Part]) -> dict[str, object]:
    return {KEY: _packed(joined(passed, parts)), VERSION_KEY: VERSION}
