"""_push_build_dirs.py -- the cargo build dirs a push export's workspaces use, as cargo names them.

A stable push export (`<run dir>/<repo>`) exists only while a push runs, but the build dir cargo
keys on its path is kept warm between pushes on purpose (BACKLOG 5.1). A reclaimer cannot ask
cargo which build dir a path that does not exist would use -- cargo canonicalises the manifest
path, so the path must exist -- and so between pushes it could not tell the export's warm build
dir from an orphan: scripts' build-root ceiling evicted every one an hour after its push
(2026-10-17). The push gate asks while the export exists and records the answer beside the
checkout in `<run dir>.source`, one `build-dir <path>` line per workspace.

The manifests are the TRACKED ones (`git ls-files`, no walk). A member of an answered workspace is
not asked again. Each `cargo metadata` is bounded; a cargo that is missing, slow or broken records
nothing, and the push goes on.

A SHIP SHARES THE CHECKOUT'S (GOH.BUILDDIRSHARE). The export kept a full second copy of every
checkout it pushed (routines 19+11 GB, divoomd 12+11, multitop 22+10; DISK.BUILDGC 2026-11-11). A
`goh ship` push gates the main checkout's own HEAD under the land lock, and its install builds that
commit in the checkout next, so the push gate points the export at the build dir cargo names for the
checkout (`--shared`): only when the checkout is clean at the pushed commit and is one workspace.
Cargo judges a path crate fresh by mtime, so an uncommitted edit older than the export's build
would read as fresh in the checkout; after the gate, `--touch-changed` gives every tracked file that
differs from HEAD the current time (only ever newer), closing the window the clean check cannot.

usage: _push_build_dirs.py <export tree>                prints one build dir per line
       _push_build_dirs.py --shared <checkout> <sha>    the checkout's one build dir, or nothing
       _push_build_dirs.py --touch-changed <checkout>   now-mtime every tracked file off HEAD
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
from _gitutil import foreign_repo_env

TIMEOUT_S = 20


def build_dirs(tree: str) -> list[str]:
    env = foreign_repo_env()  # the export is not the repo the hook runs for
    listed = subprocess.run(
        ["git", "-C", tree, "ls-files", "--", "Cargo.toml", "*/Cargo.toml"],
        env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL,
    ).stdout.split("\n")  # fmt: skip
    dirs = sorted(
        {os.path.dirname(os.path.join(tree, m)) for m in listed if m},
        key=lambda d: (d.count(os.sep), d),
    )
    found: list[str] = []
    members: set[str] = set()
    for d in dirs:
        if os.path.realpath(d) in members:
            continue
        try:
            r = subprocess.run(
                ["cargo", "metadata", "--format-version", "1", "--no-deps", "--offline"],
                cwd=d, env=env, capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
                stdin=subprocess.DEVNULL,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return found  # no cargo, or one holding its lock: record what is known
        try:
            meta = json.loads(r.stdout) if r.returncode == 0 else {}
        except ValueError:
            meta = {}
        for pkg in meta.get("packages", []):
            members.add(os.path.realpath(os.path.dirname(pkg.get("manifest_path", ""))))
        build_dir = meta.get("build_directory")
        if build_dir and build_dir not in found:
            found.append(build_dir)
    return found


def _git(tree: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", tree, *args], env=foreign_repo_env(), capture_output=True, text=True,
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def shared(checkout: str, sha: str) -> str:
    """The one build dir cargo names for CHECKOUT, when it is clean at SHA; else ''."""
    head = _git(checkout, "rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != sha:
        return ""
    dirty = _git(checkout, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        return ""
    found = build_dirs(checkout)
    return found[0] if len(found) == 1 else ""


def touch_changed(checkout: str) -> None:
    """Every tracked file of CHECKOUT that differs from HEAD, its mtime now (never older)."""
    changed = _git(checkout, "diff", "--name-only", "-z", "HEAD").stdout.split("\0")
    for name in filter(None, changed):
        path = os.path.join(checkout, name)
        if os.path.isfile(path):
            os.utime(path)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--shared":
        if got := shared(sys.argv[2], sys.argv[3]):
            print(got)
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--touch-changed":
        touch_changed(sys.argv[2])
        sys.exit(0)
    if len(sys.argv) != 2:
        sys.exit("usage: _push_build_dirs.py <export tree>")
    for path in build_dirs(sys.argv[1]):
        print(path)
