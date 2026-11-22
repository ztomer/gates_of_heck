"""A fresh checkout's coverage build starts from its repo's seed, never cold (GOH.COVCACHEWARM).

THE COST. gates/_coverage_rust.sh keys its build dir on the checkout's path (one entry per repo
root, `~/.cache/goh/llvm-cov/<key>/`), with RUSTC_WRAPPER cleared, so sccache serves none of it.
Every NEW path of a repo -- a land slot (`<landq>/slots/<repo>/<n>`, numbers past KEEP made per
land), a lane worktree, a suite worktree -- built every dependency from cold: since GOH.LANDRUSTCOV
the first Rust land in a slot paid 440 s for it at load 140-160, where a warm one pays ~41 s.

THE SEED. One entry per REPOSITORY (git's common dir), `<cache>/seed-<sha256(common)[:16]>/`,
holds a copy of the last good build of any checkout of it (`build/`, `external/`). A run whose
own entry has no build yet takes a copy of the seed (`take`) before cargo starts; a run that ends
green gives its build back (`give`) when the seed is missing, older than a day, or from another
Cargo.lock or toolchain. The copies are APFS clones (`cp -c`; `--reflink=auto` on Linux): 2.1 GiB
in under a second, blocks shared until written.

WHAT IS NOT TAKEN. cargo names a workspace member's units by a hash of its path RELATIVE to the
workspace, the same in every checkout, and checks its sources by mtime against that relative
dep-info: a member built in another checkout would read as fresh here and test THAT checkout's
code (the env!(CARGO_MANIFEST_DIR) trap, rust-shared-cache: warm-lanes). So every package with a
unit that compiled a relative path, or a path under the seed's own checkout, is removed from the
copy before it goes in place; registry, git and outside-the-repo path dependencies (absolute,
the same file for every checkout) are kept, and cargo re-roots their build-script OUT_DIRs. A
build dir of a layout this does not know is not taken at all (a cold build, said).

CONCURRENCY. A run never builds in the seed: it builds in its own entry, as before. The seed is
read and written only under its marker lock (`.goh-running.lock`, the lock _head_cache's eviction
takes without waiting), so a give never swaps it under a take, and the cache's prune never evicts
it mid-copy. It is a plain entry to the prune otherwise: least recently used, and an orphan once
its common dir is gone. Pinned by tests/test_cov_seed.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _cl
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _cl.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import hashlib
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _head_cache  # gates/, beside this file

sys.path.insert(2, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
import gitrun  # lib/, git under a deadline that strands no lock

from tui.lib import counted  # the repo root, above it

PARTS = ("build", "external")  # the workspace build and the external binaries' build
SRC = ".goh-src"  # the checkout the seed's build was made in
STAMP = ".goh-stamp"  # Cargo.lock + toolchain the seed was built against
MAX_AGE_S = 86400
LOCK = ".goh-running.lock"


def _env() -> dict[str, str]:
    """A child's environment: this one with no repository named (git's variables dropped)."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(root: str, *args: str) -> str:
    try:
        r = gitrun.run_reader(list(args), repo=root, timeout=60, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def common_dir(root: str) -> str:
    """ROOT's repository: git's common dir, physical; "" outside git."""
    got = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(got) if got else ""


def seed_path(cache: str, common: str) -> str:
    return os.path.join(cache, "seed-" + hashlib.sha256(common.encode()).hexdigest()[:16])


def stamp(proj: str) -> str:
    """What a dependency build depends on beyond the sources: the lockfile and the toolchain."""
    h = hashlib.sha256()
    d = os.path.realpath(proj)
    while True:
        lock = os.path.join(d, "Cargo.lock")
        if os.path.isfile(lock):
            with open(lock, "rb") as f:
                h.update(f.read())
            break
        if os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)
    try:
        r = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, cwd=proj, env=_env(),
                           stdin=subprocess.DEVNULL, check=False, timeout=60)  # fmt: skip
        h.update(r.stdout.encode())
    except (OSError, subprocess.TimeoutExpired):
        h.update(b"no rustc")
    return h.hexdigest()


def _clone(src: str, dst: str) -> bool:
    """DST a copy of the tree SRC, its blocks shared where the file system can."""
    args = ["cp", "-cR"] if sys.platform == "darwin" else ["cp", "-R", "--reflink=auto"]
    r = subprocess.run([*args, src, dst], capture_output=True, stdin=subprocess.DEVNULL,
                       check=False)  # fmt: skip
    if r.returncode != 0:
        shutil.rmtree(dst, ignore_errors=True)
    return r.returncode == 0


def _prereqs(depfile: str) -> list[str]:
    """The prerequisites a rustc dep-info file names (make syntax, `\\ ` an escaped space)."""
    try:
        with open(depfile, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines():
        if ": " not in line or line.startswith("#"):
            continue
        rest = line.split(": ", 1)[1].replace("\\ ", "\0")
        out += [p.replace("\0", " ") for p in rest.split()]
    return out


def local_packages(build: str, src_root: str) -> tuple[list[str], str]:
    """The package dirs (`<profile>/build/<pkg>`) of BUILD whose units compiled a checkout's own
    file, and "" -- or ([], why) for a layout this does not know."""
    if not os.path.isdir(build):
        return [], f"{build} is no directory"
    under = src_root.rstrip("/") + "/"
    local: list[str] = []
    profiles = [e.path for e in os.scandir(build) if e.is_dir() and e.name != "tmp"]
    for prof in profiles:
        if os.path.isdir(os.path.join(prof, ".fingerprint")):
            return [], f"{prof} has cargo's old layout (.fingerprint/)"
        units = os.path.join(prof, "build")
        if not os.path.isdir(units):
            continue
        for pkg in os.scandir(units):
            if not pkg.is_dir():
                continue
            mine = False
            for unit in os.scandir(pkg.path):
                outdir = os.path.join(unit.path, "out")
                if not os.path.isdir(outdir):
                    continue
                for f in os.scandir(outdir):
                    if f.name.endswith(".d") and any(
                        not p.startswith("/") or p.startswith(under) for p in _prereqs(f.path)
                    ):
                        mine = True
                        break
                if mine:
                    break
            if mine:
                local.append(pkg.path)
    return local, ""


def _locked(seed: str):
    os.makedirs(seed, exist_ok=True)
    lock = open(os.path.join(seed, LOCK), "a", encoding="ascii")  # noqa: SIM115 -- held by the caller
    _head_cache._lock_order().flock(lock, "head_cache")
    return lock


def take(cache: str, key: str, root: str) -> str:
    """The seed's build copied into entry KEY when it has none: a line saying what was taken, or
    "" (nothing to take: no repository, no seed, or the entry is warm already)."""
    entry = os.path.join(cache, key)
    if os.path.isdir(os.path.join(entry, "build")):
        return ""
    common = common_dir(root)
    if not common:
        return ""
    seed = seed_path(cache, common)
    if not os.path.isdir(os.path.join(seed, "build")):
        return ""
    t0 = time.monotonic()
    with _locked(seed) as _lock:
        try:
            with open(os.path.join(seed, SRC), encoding="utf-8") as f:
                src_root = f.read().strip()
        except OSError:
            return ""
        taken, dropped = [], 0
        for part in PARTS:
            src, dst = os.path.join(seed, part), os.path.join(entry, part)
            if not os.path.isdir(src) or os.path.exists(dst):
                continue
            tmp = os.path.join(entry, f".seed-{part}-{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(entry, exist_ok=True)
            if not _clone(src, tmp):
                return f"coverage seed: could not copy {src} -- a cold build"
            local, why = local_packages(tmp, src_root)
            if why:
                shutil.rmtree(tmp, ignore_errors=True)
                return f"coverage seed: not taken, {why} -- a cold build"
            for pkg in local:
                shutil.rmtree(pkg, ignore_errors=True)
            dropped += len(local)
            os.rename(tmp, dst)
            taken.append(part)
        open(os.path.join(seed, ".goh-used"), "w").close()
    if not taken:
        return ""
    left = counted(dropped, "checkout package")
    return (f"coverage seed: {'+'.join(taken)} from {src_root} ({left} left to build) in "
            f"{time.monotonic() - t0:.1f} s")  # fmt: skip


def give(cache: str, key: str, root: str, proj: str, pid: int) -> str:
    """Entry KEY's build copied into its repository's seed when the seed is missing, stale or from
    another lockfile; only from a linked worktree's repository and only when no other run uses the
    entry. A line saying so, or ""."""
    entry = os.path.join(cache, key)
    if not os.path.isdir(os.path.join(entry, "build")):
        return ""
    common = common_dir(root)
    linked = os.path.join(common, "worktrees") if common else ""
    if not linked or not os.path.isdir(linked) or not os.listdir(linked):
        return ""  # one checkout of its repo: no other path to warm
    others = [u for u in _head_cache._users(entry, ".goh-running") if u.split()[0] != str(pid)]
    if any(_head_cache._pid_owner().alive_line(u) for u in others):
        return ""  # another crate of the repo may be building in it right now
    seed = seed_path(cache, common)
    want = stamp(proj)
    with _locked(seed) as _lock:
        try:
            with open(os.path.join(seed, STAMP), encoding="ascii") as f:
                have = f.read().strip()
            age = time.time() - os.stat(os.path.join(seed, STAMP)).st_mtime
        except OSError:
            have, age = "", 0.0
        if have == want and age < MAX_AGE_S and os.path.isdir(os.path.join(seed, "build")):
            return ""
        for part in PARTS:
            src = os.path.join(entry, part)
            if not os.path.isdir(src):
                continue
            tmp = os.path.join(seed, f".new-{part}-{os.getpid()}")
            old = os.path.join(seed, f".old-{part}-{os.getpid()}")
            shutil.rmtree(tmp, ignore_errors=True)
            if not _clone(src, tmp):
                return ""
            dst = os.path.join(seed, part)
            if os.path.exists(dst):
                os.rename(dst, old)
            os.rename(tmp, dst)
            shutil.rmtree(old, ignore_errors=True)
        for name, text in ((SRC, os.path.realpath(root)), (".goh-root", common), (STAMP, want)):
            with open(os.path.join(seed, name), "w", encoding="utf-8") as f:
                f.write(text + "\n")
        open(os.path.join(seed, ".goh-used"), "w").close()
    return f"coverage seed: {seed} refreshed from this build"


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "take":
        line = take(argv[1], argv[2], argv[3])
    elif len(argv) >= 6 and argv[0] == "give":
        line = give(argv[1], argv[2], argv[3], argv[4], int(argv[5]))
    else:
        print("usage: _cov_seed.py take CACHE KEY ROOT | give CACHE KEY ROOT PROJ PID",
              file=sys.stderr)  # fmt: skip
        return 2
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
