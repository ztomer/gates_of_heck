"""What one read of a test was OF, as a record keeps it, and which of them a diff moves
(GOH.LANDREADS).

A listing is no read of what is listed. Until 2026-11-25 a directory a test listed was kept as the
prefix of everything under it (`lib/`), an import's own directory scan among them, and a file that
read more than 64 paths was kept as their directories: over 18 replayed lands of 2026-11-25, 5,321
of the 5,362 refused tests were refused by such a prefix, 41 by a file they had opened. So a read is
kept as what it was of:

    a/b.py     the bytes of that file (an `open`; a module's bytecode is its source)
    a/*        the NAMES in a/ (a listing; `*` is the root's): an entry that appears or goes moves
               it, an edit of a file in it does not. What the test then opens is its own read
    a/         everything under a/: a directory opened (its children are reached by an fd no event
               names), or handed to a child process, whose own reads nobody sees
    import:m   the module m was looked for: an entry that appears or goes by that name moves it

`Moved` is a diff as these words: built of what git says between the two trees, or, where git cannot
say, of every changed path as if it were new. Pinned by tests/test_read_tokens.py.

GOH.LANDREADS: what a test's read was of, a file's bytes (`a/b`), a directory's names (`a/*`), all
under it (`a/`) or a module looked for (`import:m`), and `Moved`, the words a diff moves: the carry
refuses a test that read one of them (tests/_read_audit.py).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

NAMES, IMPORT, WHOLE = "*", "import:", "./"


def under(rel: str) -> str:
    """The word for everything under the directory REL ("" the root: the whole tree)."""
    return f"{rel}/" if rel else WHOLE


def names(rel: str) -> str:
    """The word for the names in the directory REL ("" the root)."""
    return f"{rel}/{NAMES}" if rel else NAMES


def _above(path: str) -> list[str]:
    """PATH's directories, nearest first, the root ("") last."""
    out = []
    while path:
        path = path.rpartition("/")[0]
        out.append(path)
    return out


def _modules(entry: str) -> list[str]:
    """Every dotted name an import could have found ENTRY (a path that came or went) by."""
    stem = entry.removesuffix("/__init__.py").removesuffix(".py")
    parts = stem.split("/")
    parts[-1] = parts[-1].split(".")[0]  # an extension module's abi tag
    return [".".join(parts[i:]) for i in range(len(parts))]


class Moved:
    """The words CHANGED moves. ENTRIES: the paths, directories too, that came or went; None: not
    known, so each changed path and each directory above it may have."""

    def __init__(self, changed: Iterable[str], entries: Iterable[str] | None = None) -> None:
        self.words: dict[str, str] = {}
        put = self.words.setdefault
        changed = list(changed)
        for p in changed:
            put(p, p)
            for d in _above(p):
                put(under(d), p)
        if entries is None:
            came = {e: p for p in changed for e in [p, *_above(p)[:-1]]}
        else:
            came = {e: next((p for p in changed if p == e or p.startswith(e + "/")), e)
                    for e in entries}  # fmt: skip
        for e, p in came.items():
            put(e, p)  # a path that is a directory on one side, opened as a file on the other
            put(names(e.rpartition("/")[0]), p)
            for m in _modules(e):
                put(IMPORT + m, p)

    def hit(self, reads: Iterable[str]) -> str:
        """The changed path one of READS is moved by, or ""."""
        return next((self.words[r] for r in reads if r in self.words), "")


def _git(root: Path, *args: str) -> str | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    return None if r.returncode else r.stdout


def _dirs(paths: str) -> set[str]:
    return {d for p in paths.split("\0") for d in _above(p)}


def moved(root: Path, sha: str, changed: list[str]) -> Moved:
    """What the diff SHA..HEAD, which changes CHANGED, moves; unknown entries where git cannot say
    (a name git does not know), or says another set of paths."""
    said = _git(root, "diff", "--name-status", "--no-renames", "-z", sha, "HEAD")
    trees = [_git(root, "ls-tree", "-r", "-z", "--name-only", t) for t in (sha, "HEAD")]
    if said is None or None in trees:
        return Moved(changed)
    words = said.split("\0")
    status = dict(zip(words[1::2], words[::2], strict=False))
    if not set(changed) <= set(status):
        return Moved(changed)
    was, now = (_dirs(t or "") for t in trees)
    entries = {p for p, s in status.items() if s != "M"}
    return Moved(changed, entries | (was ^ now))
