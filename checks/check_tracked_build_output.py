#!/usr/bin/env python3
"""A TRACKED file that is build output: the shape a build leaves behind, committed by accident.

koffee_oss asked for this on 2026-10-19 (BACKLOG.md, "no tracked build output"): six files under
`DerivedDataTests/` came in with the history flatten, and the `.gitignore` check cannot see them --
it cannot see a file that is ALREADY tracked, which is the only case that matters.

    check_tracked_build_output.py [--root DIR] [--staged]

`--staged` is the commit hook's scope: only the files the commit adds or changes are judged, read
from the INDEX (never the worktree), because a red on a file committed long ago would block every
unrelated commit. The whole-tree run is the push's, and it names the old ones.

Exit 0: no tracked file looks like build output.
Exit 1: findings, each naming the file and the shape it matched.
Exit 2: usage/config error (or: this is not a git repository).

THE SHAPES. A directory a build writes into: `target/` (Rust, Maven, Gradle), `DerivedData/`
(Xcode), `__pycache__/` (Python), `node_modules/` (npm), `.build/` (SwiftPM), `.venv/`. A compiled
or bundled file: `.o .obj .a .so .dylib .dll .pyc .pyo .class .xcactivitylog`, and the `.dSYM`,
`.app` and `.ipa` bundles.

ONE EXTENSION, TWO KINDS OF FILE. `.obj` is a compiler's object file (COFF) and also a Wavefront mesh
(plain text, `v 0 0 0` / `f 1 2 3`): ZeroThunder tracks 244 meshes under `assets/meshes/`, and the
name alone refused every one of them (OW.29, 2026-10-31). For an extension in `AMBIGUOUS_SUFFIXES`
the CONTENT decides: a binary object starts with a magic number or holds NUL bytes, and a text file
does neither. An extension that names no text format (`.o`, `.so`, `.class`...) is still decided by
name alone, and so is a file inside a build directory.

WHAT IT IS NOT. It reads the TRACKED files (`git ls-files`), so it says nothing about a build
directory that is merely ON DISK: that is what `.gitignore` is for, and `goh`'s tracked-ignored
step already covers a file that is tracked AND ignored. And a repository that tracks nothing is
REFUSED, not passed: a scan that found no subject has not judged anything.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env, listed_files, repo_root

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok

# A directory a build writes into.
DIR_SEGMENTS = (
    "target",
    "DerivedData",
    "__pycache__",
    "node_modules",
    ".build",
    ".venv",
)

# A compiled file.
FILE_SUFFIXES = (
    ".o",
    ".obj",
    ".a",
    ".so",
    ".dylib",
    ".dll",
    ".pyc",
    ".pyo",
    ".class",
    ".xcactivitylog",
)

# The suffixes above that ALSO name a text format, so the name alone cannot say. These are decided
# by the file's content (`is_binary_object`); every other FILE_SUFFIXES entry is decided by name.
AMBIGUOUS_SUFFIXES = (".obj",)

# The first bytes of a compiler's object file, for one that holds no NUL in the window below.
OBJECT_MAGIC = (
    b"\x7fELF",  # ELF
    b"\xfe\xed\xfa\xce",  # Mach-O 32, either byte order
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",  # Mach-O 64, either byte order
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",  # Mach-O universal
    b"\x4c\x01",  # COFF: i386
    b"\x64\x86",  # COFF: x86-64
    b"\x64\xaa",  # COFF: arm64
    b"\xc4\x01",  # COFF: arm Thumb-2
    b"!<arch>\n",  # an archive
)

# How much of a file is read to tell a binary from a text file (git's own window is 8000).
SNIFF_BYTES = 8192

# A bundle a build produced.
BUNDLE_SUFFIXES = (".dSYM", ".app", ".ipa")


def tracked(root):
    """The repository's tracked files, or None when git could not answer."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "-z"],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=foreign_repo_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.split("\0") if p]


def is_binary_object(head):
    """Whether `head` (a file's first bytes) is a compiler's object file, not a text file.

    A NUL byte in the window, or a known object-file magic number. An EMPTY file is neither: no
    compiler writes one, and a repository that tracks an empty `x.obj` has not tracked build output.
    """
    return head.startswith(OBJECT_MAGIC) or b"\0" in head[:SNIFF_BYTES]


def blobs(root, paths, env):
    """{path: first SNIFF_BYTES of its INDEX blob, or None when unreadable}, by ONE git process.

    `git cat-file --batch` reads `:path` from the index, so a staged check judges what the commit
    holds. One process for all of them: ZeroThunder has 244 ambiguous files, and a `git show` each
    is 244 spawns on every commit."""
    heads = {}
    if not paths:
        return heads
    names = [p for p in paths if "\n" not in p]  # a newline in a name cannot be asked of --batch
    try:
        out = subprocess.run(
            ["git", "-C", root, "cat-file", "--batch"],
            input="".join(f":{p}\n" for p in names).encode("utf-8", "surrogateescape"),
            capture_output=True,
            check=False,
            env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {p: None for p in paths}
    at = 0
    for name in names:
        eol = out.find(b"\n", at)
        header = out[at:eol].split() if eol != -1 else []
        if len(header) == 3 and header[2].isdigit():  # `<oid> <type> <size>`, then the body
            size = int(header[2])
            is_blob = header[1] == b"blob"  # a gitlink named x.obj is a commit: no content to sniff
            heads[name] = out[eol + 1 : eol + 1 + min(size, SNIFF_BYTES)] if is_blob else None
            at = eol + 1 + size + 1
        else:  # `<spec> missing`: one line, no body
            heads[name] = None
            at = eol + 1 if eol != -1 else len(out)
    return {p: heads.get(p) for p in paths}


def in_build_dir(path):
    """Whether a path is inside a build directory or bundle: those are decided without content."""
    parts = path.split("/")
    return any(s in DIR_SEGMENTS for s in parts[:-1]) or any(
        s.endswith(BUNDLE_SUFFIXES) for s in parts
    )


def findings(paths, heads=None):
    """(path, why) for every tracked file that looks like build output.

    `heads` maps an AMBIGUOUS path to its first bytes (None: unreadable). A path of an ambiguous
    extension with no entry is judged by name, as every path was before content was read."""
    heads = heads or {}
    out = []
    for path in paths:
        parts = path.split("/")
        segment = next((s for s in parts[:-1] if s in DIR_SEGMENTS), None)
        if segment is not None:
            out.append((path, f"inside a build directory ({segment}/)"))
            continue
        bundle = next((s for s in parts if s.endswith(BUNDLE_SUFFIXES)), None)
        if bundle is not None:
            out.append((path, f"a build bundle ({bundle})"))
            continue
        if path.endswith(AMBIGUOUS_SUFFIXES) and path in heads:
            head = heads[path]
            if head is None:  # a verdict needs the content; no content, no pass
                out.append(
                    (path, "a compiled file (its content could not be read to say it is not)")
                )
            elif is_binary_object(head):
                out.append((path, "a compiled file (a binary object, not a text format)"))
            continue
        if path.endswith(FILE_SUFFIXES):
            out.append((path, "a compiled file"))
            continue
    return out


def staged_paths(root):
    """The files the commit adds or changes, from the index the hook carried (ambient git env)."""
    try:
        return listed_files(root, staged=True)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return None


def judged(root, staged):
    """(paths to judge, the git env that reads their blobs), or (None, None) when git failed.

    Whole tree: every tracked file, under `foreign_repo_env` (the checked repo may not be the one a
    hook is running for). Staged: what the commit brings, under the AMBIENT env, because there
    GIT_INDEX_FILE names the index being committed -- the tree to police (contract 12)."""
    if staged:
        paths = staged_paths(root)
        return paths, dict(os.environ)
    return tracked(root), foreign_repo_env()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=None, help="the repository to judge (default: this one)")
    parser.add_argument(
        "--staged",
        action="store_true",
        help="judge only what the commit adds or changes (pre-commit)",
    )
    args = parser.parse_args(argv)
    root = args.root or repo_root()
    paths, env = judged(root, args.staged)
    if paths is None:
        err("tracked build output: git could not list the tracked files -- nothing judged")
        return 2
    if not paths and not args.staged:
        err("tracked build output: this repository tracks no files -- nothing to judge")
        return 1
    ambiguous = [p for p in paths if p.endswith(AMBIGUOUS_SUFFIXES) and not in_build_dir(p)]
    found = findings(paths, blobs(root, ambiguous, env))
    if found:
        err(
            f"tracked build output: {len(found)} {'staged' if args.staged else 'tracked'} file(s) look like build output:"
        )
        for path, why in found:
            err(f"  {path} -- {why}")
        err("  a build's output is not source: take it out of the tree, and let .gitignore keep it")
        return 1
    kind = "staged" if args.staged else "tracked"
    ok(f"tracked build output: none of the {len(paths)} {kind} file(s) looks like build output")
    return 0


if __name__ == "__main__":
    sys.exit(main())
