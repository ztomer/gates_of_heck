#!/usr/bin/env python3
"""path_invariance.py — a gate's verdict must not depend on how the tree's path is spelled.

One directory has several spellings on macOS: the canonical one `getcwd` returns
(`/private/var/folders/...`), a symlink's (`/var/folders/...`, `/tmp/...`) and the data volume's
firmlink (`/System/Volumes/Data/...`). A tool that subtracts `getcwd` from a path it built from the
spelling it was handed gets a different answer under each: ZeroThunder's SwiftLint 0.65.1 keyed its
baseline that way, and from `/private/tmp` all 1034 recorded violations read as new while from
`/Users` none did (BACKLOG 4.7). The tree was the same; the verdict was not.

This clones the COMMITTED tree (HEAD) to scratch and runs the command once under every spelling of
that clone the host offers, each with `PWD` set to it as a shell's `cd` sets it, and `{root}` in the
command replaced by it. Two verdicts (exit codes) are a finding. A command that fails everywhere is
one verdict: whether it SHOULD fail is its own gate's question, not this one's.

    path_invariance.py -- swiftlint lint --strict
    path_invariance.py -- ./tools/lint.sh {root}

Exit 0 one verdict, 1 the verdict depends on the spelling, 2 a precondition (no command, not a git
checkout, a host that offers one spelling only -- which proves nothing, so it never passes).
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

import glob
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))
import pid_owner  # a saved pid is the owner only while its birth still matches
import step_timings  # a row per clone and per spelling (GOH.RELEASELANDGATE)
from tmp_root import goh_tmp  # never Spotlight-indexed (GOH.TMPNOINDEX)

NAME = "path_invariance"
# The symlinks macOS ships from the root into /private: a path under /private/<x> is also /<x>.
PRIVATE_ALIASES = ("tmp", "var", "etc")
# The data volume's firmlinked mount: every path on it is also spelled under this prefix.
DATA_VOLUME = "/System/Volumes/Data"
# How much of a differing spelling's output to show: its tail, where a tool says why it failed.
TAIL_LINES = 6
# Each scratch copy names its owner, and a copy whose owner is gone is swept by the next run: a run
# killed past its `finally` (SIGKILL, a step ceiling) left 22 MB of copies under ~/.cache/goh
# (GOH.PATHINVLEAK). A copy with no owner file predates this, and goes once it is this old.
PREFIX = "goh-path-invariance."
OWNER = ".owner"
UNOWNED_STALE_S = 6 * 3600


def _stale(path: str, now: float) -> bool:
    try:
        with open(os.path.join(path, OWNER), encoding="utf-8") as fh:
            line = fh.read()
    except OSError:
        try:
            return now - os.stat(path).st_mtime > UNOWNED_STALE_S
        except OSError:
            return False
    return not pid_owner.alive_line(line)  # `<pid> <lstart>`: a reused number is not the owner


def sweep(parent: str) -> None:
    """Remove the scratch copies under `parent` whose run is gone."""
    now = time.time()
    for path in glob.glob(os.path.join(glob.escape(parent), PREFIX + "*")):
        if os.path.isdir(path) and not os.path.islink(path) and _stale(path, now):
            shutil.rmtree(path, ignore_errors=True)


def scratch_dir(parent: str) -> str:
    """A fresh scratch copy under `parent`, after sweeping the stale ones, naming its owner."""
    sweep(parent)
    path = tempfile.mkdtemp(prefix=PREFIX, dir=parent)
    with open(os.path.join(path, OWNER), "w", encoding="utf-8") as fh:
        fh.write(f"{pid_owner.stamp_line(os.getpid()) or os.getpid()}\n")
    return path


def _exit_on_signal(signum, _frame):
    """A TERM or HUP (a step ceiling, a closed terminal) unwinds through `finally`."""
    raise SystemExit(128 + signum)


def spellings(real: str) -> list[str]:
    """Every spelling of `real` (a canonical path) that names the same directory on this host."""
    found = [real]
    parts = real.split("/")
    if len(parts) > 2 and parts[1] == "private" and parts[2] in PRIVATE_ALIASES:
        found.append("/" + "/".join(parts[2:]))
    found.append(DATA_VOLUME + real)
    return [p for p in found if os.path.isdir(p) and os.path.samefile(p, real)]


def clone_head(root: str, dest: str) -> str:
    """A clone of `root`'s HEAD at `dest`, sharing its objects; the source repository is only read."""
    head = subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    env = foreign_repo_env()
    t0 = time.monotonic()
    for argv in (
        ["git", "clone", "-q", "--shared", "--no-checkout", root, dest],
        ["git", "-C", dest, "checkout", "-q", "--detach", head],
    ):
        subprocess.run(argv, capture_output=True, check=True, env=env, stdin=subprocess.DEVNULL)
    step_timings.record(f"{NAME}: clone HEAD", (time.monotonic() - t0) * 1000, 0, "phase")
    return head


def verdict(command: list[str], spelled: str) -> subprocess.CompletedProcess:
    argv = [part.replace("{root}", spelled) for part in command]
    env = foreign_repo_env()
    env["PWD"] = spelled
    # Each run is a row its own rows nest under: the step's profile names where its seconds went.
    label = f"{NAME}: under {spelled}"
    if env.get(step_timings.ENV):
        env[step_timings.PARENT_ENV] = label
    t0 = time.monotonic()
    r = subprocess.run(
        argv,
        cwd=spelled,
        env=env,
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        stdin=subprocess.DEVNULL,
    )
    step_timings.record(label, (time.monotonic() - t0) * 1000, r.returncode, "phase")
    return r


USAGE = "usage: path_invariance.py -- <gate command> ({root} is the tree)"
HELP_FLAGS = ("-h", "--help")


def main(argv: list[str]) -> int:
    if "--" not in argv and argv[:1] and argv[0] in HELP_FLAGS:
        print(USAGE)
        return 0
    command = argv[argv.index("--") + 1 :] if "--" in argv else argv
    if not command:
        print(f"✗ [{NAME}] {USAGE}", file=sys.stderr)
        return 2
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if top.returncode != 0:
        print(f"✗ [{NAME}] not inside a git checkout: {top.stderr.strip()}", file=sys.stderr)
        return 2
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)
    scratch = scratch_dir(goh_tmp())
    home_cache = os.path.expanduser("~/.cache/goh")
    home_scratch = None
    try:
        tree = os.path.join(os.path.realpath(scratch), "tree")
        head = clone_head(top.stdout.strip(), tree)
        names = spellings(tree)
        if len(names) < 2:
            # A host with no alias of its own (Linux: /tmp is no symlink) gets one: a symlink to the
            # tree is a spelling of it everywhere, so `$PWD` and getcwd() still differ under it
            # (GOH.WORKERFAILS: on the Linux worker the cwd-keyed gate read as invariant).
            link = os.path.join(os.path.realpath(scratch), "spelled")
            os.symlink(tree, link)
            names.append(link)
        if not os.path.realpath(home_cache).startswith("/private/"):
            try:
                os.makedirs(home_cache, exist_ok=True)
                home_scratch = scratch_dir(home_cache)
                home_tree = os.path.join(os.path.realpath(home_scratch), "tree")
                clone_head(top.stdout.strip(), home_tree)
                names.append(home_tree)
            except OSError:
                pass
        if len(names) < 2:
            print(
                f"✗ [{NAME}] this host spells {tree} one way only, so nothing was compared",
                file=sys.stderr,
            )
            return 2
        try:
            runs = [(name, verdict(command, name)) for name in names]
        except OSError as e:
            # A gate that cannot start fails identically under every spelling, which would read as
            # one verdict -- a pass for the check -- so it is named instead.
            print(f"✗ [{NAME}] cannot run `{' '.join(command)}`: {e.strerror}", file=sys.stderr)
            return 2
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if home_scratch:
            shutil.rmtree(home_scratch, ignore_errors=True)
    codes = {r.returncode for _, r in runs}
    shown = " ".join(command)
    if len(codes) == 1:
        print(
            f"✓ [{NAME}] `{shown}` gives one verdict (exit {codes.pop()}) under {len(runs)} spellings of {head[:12]}"
        )
        return 0
    print(
        f"✗ [{NAME}] `{shown}` gives a different verdict depending on how the tree's path is spelled:",
        file=sys.stderr,
    )
    for name, r in runs:
        print(f"    exit {r.returncode}  {name}", file=sys.stderr)
    first = runs[0][1].returncode
    for name, r in runs:
        if r.returncode != first:
            tail = (r.stdout + r.stderr).strip().splitlines()[-TAIL_LINES:] or ["(no output)"]
            print(f"  under {name}:", *(f"    {line}" for line in tail), sep="\n", file=sys.stderr)
            break
    print(
        "  a path the tool built from the spelling it was handed is being compared with getcwd(), "
        "which is always canonical: make both sides one spelling (realpath), or key on content",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
