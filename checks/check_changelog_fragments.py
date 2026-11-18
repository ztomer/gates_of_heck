#!/usr/bin/env python3
"""Every changelog fragment is one the release step accepts (GOH.FRAGGATE).

    check_changelog_fragments.py [--root DIR] [--staged]

The release preflight (`tools/release-kit/changelog_fragments.py` `read`) refuses a fragment with a
heading or with no entry, but it runs only at ship time: a lane committed and landed
`changelog/GOH.TEMPLATEQUIET.md` with a heading, and the ship of 93d3c7aa refused it. This runs the
SAME rule (`changelog_fragments.refusal`, imported, never copied) over `changelog/*.md` and
`docs/changelog/*.md`, the README excepted: at `--staged` the blobs the commit adds or changes,
read from the index; at full every such file in the tree. No fragment is nothing to judge, and
passes.

Exit 0: every fragment is entries only. Exit 1: findings, one per fragment. Exit 2: not a repo.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT, os.path.join(ROOT, "tools", "release-kit")):
    sys.path.insert(0, _p)

_gitutil = importlib.import_module("_gitutil")
_tui = importlib.import_module("tui.lib")
# The release kit's own reader: the rule is imported, never copied.
_frag = importlib.import_module("changelog_fragments")


def findings(root: str, staged: bool) -> tuple[int, list[str]]:
    """(fragments read, findings) over the scope."""
    names = [n for n in _gitutil.listed_files(root, staged) if _frag.is_fragment(n)]
    out = []
    for rel in names:
        blob = _gitutil.content_bytes(root, rel, staged)
        try:
            text = (blob or b"").decode("utf-8")
        except UnicodeDecodeError as e:
            out.append(f"{rel}: cannot be read as UTF-8 text: {e}")
            continue
        why = _frag.refusal(text.splitlines())
        if why:
            out.append(f"{rel}: {why}")
    return len(names), out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=None)
    ap.add_argument("--staged", action="store_true", help="judge the index's changed fragments")
    args = ap.parse_args(argv)
    root = args.root or _gitutil.repo_root()
    if not root:
        _tui.err("changelog fragments: not a git repository")
        return 2
    read, found = findings(root, args.staged)
    for f in found:
        _tui.err(f"changelog fragment {f}")
    if found:
        _tui.err(
            f"changelog fragments: {_tui.noun(len(found), 'fragment')} the release would refuse; "
            "a fragment is `* ` entries only, no heading"
        )
        return 1
    _tui.ok(f"changelog fragments: {_tui.noun(read, 'fragment')} read, all entries only")
    return 0


if __name__ == "__main__":
    # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py).
    sys.path.insert(0, os.path.join(ROOT, "gates"))
    with contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout
        __import__("_from_head").reexec(__file__)
    sys.exit(main())
