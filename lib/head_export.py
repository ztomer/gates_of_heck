#!/usr/bin/env python3
"""A sibling repository at its HEAD commit, for a consumer's gate: never its working tree.

THE DEFECT (GOH.HEADEXPORT, from ZT0.GAFHEAD). ZeroThunder's gate imported game_asset_factory from
the live checkout, and a concurrent agent's UNCOMMITTED edit there (`NameError: name 'counted'`)
failed ZeroThunder's push for code in neither repo's history (2026-11-11). Three more consumers
pointed a gate at a sibling's working tree the same way. goh removed this defect for its OWN gates
long ago (gates/_from_head.sh, contract C4); this is the same mechanism, public, for any sibling.

WHAT IT MAKES. `<cache>/<sha>/`: a local `git clone` of SRC checked out DETACHED at SRC's HEAD,
built in a `.build.*` temp dir, made read-only (every directory and file but `.git`, whose objects
are hard links to SRC's own), and renamed into place whole. A CLONE, not `git archive`: a sibling
may find its own root through git (the factory's `game_root(__file__)`), and an archive answered
`RootNotFound`. `.head-export` names the commit and the source; `.head-used` is the last-use stamp,
the only file ever rewritten. An uncommitted edit or an untracked file in SRC cannot reach it.

THE CEILING. Each NEW export prunes the cache with goh's one policy (gates/_head_cache.py): the
current one stays, the KEEP most recently used stay, nothing used within the hour goes, and nothing
unused for a week stays. Eviction goes through `goh_rm_tree` (lib/rm_tree.sh), which makes the tree
writable first: `rmtree(ignore_errors=True)` leaves a read-only tree in place and calls it removed.

Usage (a consumer's gate; the shell wrapper is lib/head_export.sh):

    . "$GOH_DIR/lib/head_export.sh"
    goh_head_export GAF_DIR "$GAF_SRC" || exit 2      # GAF_DIR is now the export's path

    python3 lib/head_export.py SRC [--cache DIR] [--keep N]   # prints the export's path

SRC already an export (a gate nested in a gate) is answered as is, so one run judges with one
commit. The default cache is `${XDG_CACHE_HOME:-~/.cache}/goh/sibling/<basename of SRC>`. Git runs
with the hook's repository variables dropped (contract #12): under a pre-push hook,
`git -C SRC rev-parse HEAD` would otherwise answer the CONSUMER's HEAD. Refuses, exit 2 with a line
on stderr, when SRC is not a checkout with a HEAD commit: a gate that cannot name the commit it
judges with must not judge with an unnamed one.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import stat
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_GOH = os.path.dirname(_HERE)

MARKER = ".head-export"  # line 1 the sha, line 2 the source's real path
USED = ".head-used"  # the last-use stamp
KEEP = 8  # the factory's export is ~4 MB (4290560 bytes at 8a26870, measured 2026-11-12)
RM_TREE = os.path.join(_HERE, "rm_tree.sh")


class Refused(Exception):
    """SRC cannot name a commit."""


def _goh_import(dirname: str, module: str):
    """goh's own modules: the prune policy (gates/) and the foreign-repo env (checks/)."""
    path = os.path.join(_GOH, dirname)
    if path not in sys.path:
        sys.path.insert(0, path)
    return __import__(module)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    foreign_repo_env = _goh_import("checks", "_gitutil").foreign_repo_env  # contract #12
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=foreign_repo_env(),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def default_cache(src: str) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "goh", "sibling", os.path.basename(os.path.realpath(src)))


def is_export(path: str) -> bool:
    return os.path.isfile(os.path.join(path, MARKER))


def head_of(src: str) -> str:
    out = (
        _git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], src) if os.path.isdir(src) else None
    )
    if out is None or out.returncode != 0 or not out.stdout.strip():
        raise Refused(
            f"head_export: {src} is not a git checkout with a HEAD commit, so the gate cannot name"
            " the commit it would judge with"
        )
    return out.stdout.strip()


def remove(path: str) -> None:
    """goh_rm_tree: make the tree writable (directories only), then remove it."""
    subprocess.run(
        ["/bin/bash", "-c", '. "$1"; goh_rm_tree "$2"', "_", RM_TREE, path],
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _read_only(root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        if dirpath == root and ".git" in dirnames:
            dirnames.remove(".git")  # hard links to the source's objects: never chmod them
        for name in filenames:
            if dirpath == root and name == USED:
                continue
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) & ~0o222)
    for dirpath, _dirnames, _f in os.walk(root, topdown=False):
        if dirpath == root or os.path.relpath(dirpath, root).split(os.sep)[0] == ".git":
            continue
        os.chmod(dirpath, 0o555)
    os.chmod(root, 0o755)  # the root keeps u+w: the last-use stamp is rewritten there


def build(src: str, sha: str, cache: str, keep: int = KEEP) -> str:
    dest = os.path.join(cache, sha)
    if is_export(dest):
        return dest
    os.makedirs(cache, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".build.", dir=cache)
    try:
        for args, cwd in (
            (["clone", "-q", "--no-checkout", src, tmp], cache),
            (["checkout", "-q", "--detach", sha], tmp),
        ):
            out = _git(args, cwd)
            if out.returncode != 0:
                raise Refused(f"head_export: git {args[0]} failed: {out.stderr.strip()}")
        with open(os.path.join(tmp, ".git", "info", "exclude"), "a") as fh:
            fh.write(f"/{MARKER}\n/{USED}\n")  # the export's own files; its status stays clean
        with open(os.path.join(tmp, MARKER), "w") as fh:
            fh.write(f"{sha}\n{os.path.realpath(src)}\n")
        open(os.path.join(tmp, USED), "w").close()
        _read_only(tmp)
        if os.path.lexists(dest) and not is_export(dest):
            remove(dest)  # a half-made or foreign directory at the sha is not an export
        try:
            os.rename(tmp, dest)  # whole, or not at all; a lost race left the same commit there
        except OSError:
            remove(tmp)
    except BaseException:
        remove(tmp)
        raise
    if not is_export(dest):
        raise Refused(f"head_export: {dest} could not be made an export")
    _goh_import("gates", "_head_cache").prune(
        cache, sha, keep=keep, stamps=(USED, MARKER), remove=remove
    )
    return dest


def resolve(src: str, cache: str | None = None, keep: int = KEEP) -> str:
    dest = src if is_export(src) else build(src, head_of(src), cache or default_cache(src), keep)
    with contextlib.suppress(OSError):  # an export someone else owns still judges
        open(os.path.join(dest, USED), "w").close()
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="export a sibling repo at its HEAD commit")
    ap.add_argument("src")
    ap.add_argument("--cache", default="")
    ap.add_argument("--keep", type=int, default=KEEP)
    a = ap.parse_args(argv)
    try:
        print(resolve(a.src, a.cache or None, a.keep))
    except (Refused, OSError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
