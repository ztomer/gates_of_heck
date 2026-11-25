"""lib/commit_key.py -- the key a commit gate's green verdict is stamped by (GOH.STAMPREBASE).

WHY. A commit was stamped by its whole TREE, so a rebase over anything voided every stamp of the
lane: GOH.PYSLOW4's 18 commits were gated again, one by one, in about 400 of its 483 s enqueue check
(2026-11-25). And a tree alone names no change: a commit no gate judged, squashed into a judged one
(or amended, its hook seeing only the amendment), had a stamped tree.

THE KEY is what the gate read: `c1:` and the sha256 of
  the change   each path of `diff-tree PARENT TREE`: its modes, its blobs before and after, its
               status, so a commit whose touched file moved under it is another change
  the rest     every path of TREE, by mode and blob, less what the gate does not read (UNREAD)
A rebase over a change to UNREAD paths alone keeps the key; over any other it does not, and the
commit is gated again. A conflict resolved in the rebase is another change. The gate's code, its
seeds, .gatesrc and whatever a guard reads are in the tree, so they are in the key.

WHAT A COMMIT'S GATE READS BEYOND ITS DIFF, measured 2026-11-25 (every file open and git object read
of a goh commit's `structural --staged`, one step at a time, and of each staged guard):
  no unreaped spawns in tests     the blob of every .py, .rs, .sh and .bash of the index (1,802)
  every decision record is whole  docs/decisions/, and `git grep` over the whole index
  no bare read of the hook's index  opens every tracked file of the WORK TREE for its first bytes
  shell lint                      the staged shell's sourcing files (a commit that stages shell)
  every language linted ... push  .gatesrc, .swiftlint.yml and what GOH_CI_STEPS reaches (37)
  prose claims, Unreleased, tags, run paths, plural hedges, locale, bare open, TUI marks, kill by
  name                            their root seed or allow file, .gatesrc, CHANGELOG.md
  one roadmap                     the whole index, when the commit brings the roadmap or its seed
  markdown links                  each link's target, when the commit brings markdown
  ruff (gates/py_staged.sh)       pyproject.toml, tools/py_lint_ceilings.json
  the staged guards               test_load_sensitive, test_wall_clock_ratchet: every tests/*.py;
                                  test_config_schema, test_tmp_noindex: gates, lib, tools and more
Every other step reads the staged blobs and .gatesrc. So a code file another land changed IS read
(by the unreaped scan at least, and a goh commit's gate IS the tree's own gates/, checks/ and lib/),
and a commit rebased over one is gated again: UNREAD is the prose no step above reads for a commit
of CODE, and nothing else. The list is what is left OUT of the key, so a file of a kind nobody
thought of is in it.

UNREAD holds for a goh checkout's commit that brings code and its changelog fragment alone, none of
it naming an unread path the way a claim or a link does (`plain`); in any other repository, whose
`tools/gate.sh` may read what it likes, and for any other commit, the key is the whole tree. Pinned
by tests/test_commit_key.py, which watches the real binary's object reads.

The key of a commit gate's stamp (GOH.STAMPREBASE): the change (each touched path's modes and blobs,
before and after) and every other path of the tree less `UNREAD` (changelog fragments and the prose
under docs/ no step reads for a commit of code), so a rebase over those keeps it and over anything
the gate reads, its code, .gatesrc, a seed, a guard's file, does not. Invoked by
`lib/commit_stamp.py`.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun  # lib/: a reader under a deadline that takes no lock (GOH.GITKILL)

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
GOH = ("gates/commit_gate.sh", "gates/_from_head.sh", "crates/goh/Cargo.toml")  # _self_gate.sh
# docs/map.md: a decision lives in it, and the step asks that it is tracked
UNREAD = re.compile(r"(?:changelog/[^/]+|docs/(?!decisions/|config/|map\.md).+)\.md")
FRAGMENT = re.compile(r"changelog/[^/]+\.md")
CODE = (".py", ".sh", ".rs")
CLAIM = re.compile(r"`\d+`\s|\bclaim:")  # a marked number, any target (claims/text.rs)
LINK = "]("


def _git(repo: str | Path, *words: str, said: str | None = None) -> str:
    """git's output in REPO; RuntimeError when it fails or is no text: half a tree keys nothing."""
    try:
        done = gitrun.run_reader(words, repo=repo, timeout=60, input_text=said)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"git {words[0]}: {e}") from e
    if done.returncode != 0:
        raise RuntimeError(f"git {words[0]}: {done.stderr.strip()[:300]}")
    return done.stdout


def change(repo: str | Path, parent: str, tree: str) -> dict[str, str]:
    """{path: `modes blobs status`} of PARENT's change to TREE, a rename its two paths."""
    raw = _git(repo, "diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-abbrev", parent, tree)
    fields = raw.split("\0")[:-1]
    return dict(sorted(zip(fields[1::2], fields[::2], strict=True)))


def listing(repo: str | Path, tree: str) -> dict[str, str]:
    """{path: `mode type blob`} of every path of TREE."""
    out = _git(repo, "ls-tree", "-r", "-z", "--full-tree", tree)
    return dict(reversed(e.split("\t", 1)) for e in out.split("\0")[:-1])  # type: ignore[misc]


def plain(repo: str | Path, touched: dict[str, str]) -> bool:
    """Whether TOUCHED is code and changelog fragments alone, with no claim and no link in either:
    the commits whose gate was measured to read none of UNREAD."""
    if not all(p.endswith(CODE) or FRAGMENT.fullmatch(p) for p in touched):
        return False
    frags = [b for p, b in after(touched).items() if FRAGMENT.fullmatch(p)]
    every = list(after(touched).values())
    try:
        text, links = (_git(repo, "cat-file", "--batch", said="".join(f"{b}\n" for b in blobs))
                       if blobs else "" for blobs in (every, frags))  # fmt: skip
    except RuntimeError:
        return False  # no text: nothing says what it names
    return not CLAIM.search(text) and LINK not in links


def after(touched: dict[str, str]) -> dict[str, str]:
    """{path: its blob after} of TOUCHED, a deleted path's left out."""
    blobs = {p: what.split()[3] for p, what in touched.items()}
    return {p: b for p, b in blobs.items() if b.strip("0")}


def key(repo: str | Path, parent: str, tree: str) -> str:
    """The key of the commit that makes TREE of PARENT (tree-ishes of REPO; module doc)."""
    touched, every = change(repo, parent, tree), listing(repo, tree)
    if all(p in every for p in GOH) and plain(repo, touched):
        every = {p: what for p, what in every.items() if not UNREAD.fullmatch(p)}
    lines = [f"{what}\t{p}" for part in (touched, every) for p, what in sorted(part.items())]
    return f"c1:{hashlib.sha256(chr(0).join(lines).encode()).hexdigest()}"


def of(repo: str | Path, rev: str) -> str:
    """The key of commit REV, against its first parent (the empty tree for a root)."""
    listed = _git(repo, "rev-list", "--parents", "-n", "1", rev).split()
    return key(repo, listed[1] if len(listed) > 1 else EMPTY_TREE, listed[0])


def staged(repo: str | Path, tree: str) -> str:
    """The key of the commit a hook is judging: the index's TREE over HEAD."""
    head = gitrun.run_reader(("rev-parse", "--verify", "--quiet", "HEAD"), repo=repo, timeout=60)
    return key(repo, head.stdout.strip() if head.returncode == 0 else EMPTY_TREE, tree)
