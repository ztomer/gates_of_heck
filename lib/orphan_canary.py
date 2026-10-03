#!/usr/bin/env python3
"""Report the processes a test suite LEFT RUNNING — the orphans, which are silent by construction.

    lib/orphan_canary.py take   SNAP                 # record the live process table
    lib/orphan_canary.py since  SNAP [--repo DIR]    # what is new since that snapshot
    lib/orphan_canary.py wrap   SNAP -- CMD...       # take, run, report; exit 1 if attributable

EXIT. 0 nothing attributable survived · 1 an orphan this repo can be named as the owner of ·
2 usage error.

WHY, and why it is separate from the ceiling
--------------------------------------------
`lib/bounded_run.py` bounds a step and sweeps what the step itself spawned. It cannot help with
the case that actually cost a day: the test **finished**, so the step exited 0, and the server the
test leaked outlived it. media_server, 2026-10-03: nine live orphans, each holding the cargo build
lock, every later `cargo test` blocked with no output at all, and the run that could have reported
the leak was the run that had been killed. A leak that only shows up as a *later* hang is a leak
with no signal of its own, so this measures one.

HOW, and the honest limit
-------------------------
A before/after diff of the live process table. Everything new since the snapshot is a candidate;
a candidate is ATTRIBUTABLE when its command line names this repo — the checkout path, a `target/`
under it, or `$CARGO_TARGET_DIR`. That is the shape the incident had: the leaked binary lived at
`crates/archive-torznab-rs/target/debug/archive_torznab`.

**Measured, and the reason for the ppid rule:** an orphan's parent is gone, so on this platform it
is reparented to 1 (`ps -o ppid=`, 2026-10-03, confirmed from outside the test). Candidates with
`ppid == 1` are therefore named even when their command line says nothing about this repo, because
"a brand-new process adopted by init" is the signature of a leak and nothing else.

**What this cannot do.** On a machine running anything else, a process started by another program
during the window is a candidate, and this reports it rather than guessing. Candidates that are not
attributable are printed under a heading that says so and do NOT fail the run: a canary that cries
wolf on every run is a canary that gets switched off, and a switched-off canary is worse than none
because the estate would believe it. Only the attributable ones are a verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SNAPSHOT_VERSION = 1


def snapshot() -> dict[str, list[int]]:
    """{pid: [ppid]} for every process this user can see. One `ps`, no per-pid spawns."""
    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"orphan_canary: cannot read the process table: {exc}", file=sys.stderr)
        return {}
    table: dict[str, list[int]] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            table[parts[0]] = [int(parts[1])]
    return table


def describe(pids: list[str]) -> dict[str, str]:
    """{pid: command line} for the named pids, one `ps` for all of them."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", ",".join(pids)],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    # FULL command lines: attribution matches a PATH, and truncating at 160 chars cut the path off
    # before the token that proves it — measured: a leaked `…/target/debug/archive_torznab` under
    # pytest's tmp dir (a 190-char path) was attributed but then displayed without its own name, so
    # the report could not name what it found. Truncation is a DISPLAY decision, made at print time.
    lines = out.stdout.splitlines()
    return dict(zip(pids, (line.strip() for line in lines)))


def attributable(command: str, roots: list[str]) -> str | None:
    """The root that makes this process ours, or None."""
    for root in roots:
        if root and root in command:
            return root
    return None


def roots_for(repo: str | None) -> list[str]:
    """Every path that makes a process this repo's: the checkout, its target dirs, CARGO_TARGET_DIR."""
    out = []
    if repo:
        out.append(os.path.realpath(repo))
    env = os.environ.get("CARGO_TARGET_DIR")
    if env:
        out.append(os.path.realpath(env))
    return out


def _own_descendants() -> set[str]:
    """The canary's own subprocesses — the `ps` calls it makes while measuring.

    Excluded because they are not the subject: without this, every run reports two or three of its
    own helper processes as "new, not attributable", and a report that always has three entries in
    it is a report nobody reads. Measured on a busy machine, 2026-10-03: three unrelated `sccache`/
    `rustc` processes from another repo were also reported, which is the honest part and is why the
    unattributable half is a COUNT.
    """
    mine = str(os.getpid())
    out = subprocess.run(["ps", "-Ao", "pid=,ppid="], capture_output=True, text=True, check=False)
    children: dict[str, list[str]] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            children.setdefault(parts[1], []).append(parts[0])
    # `mine` is in the set, and it is a STRING like every key `snapshot()` produces.
    #
    # Both halves were measured wrong first. `gates/local_ci.sh` invokes this canary as its own
    # process AND passes `--repo <root>` on the same command line, so the canary matches its own
    # attribution root by path — every passing step failed, attributed to the canary watching it
    # (2026-10-03). Excluding `mine` fixed nothing, because this set held `int`s and `snapshot()`
    # keys `str`s: `{"53167"} - {53167}` is `{"53167"}`. A subtraction across two representations
    # of one value is a subtraction that does not happen, and it reads exactly like a working one.
    found, stack = {mine}, [mine]
    while stack:
        for kid in children.get(stack.pop(), []):
            if kid not in found:
                found.add(kid)
                stack.append(kid)
    return found


def report(before: dict, roots: list[str], repo: str | None) -> tuple[int, list[str], list[str]]:
    """(verdict, attributable lines, unattributable lines)."""
    after = snapshot()
    skip = _own_descendants()
    new = sorted(set(after) - set(before) - skip, key=int)
    if not new:
        return 0, [], []
    commands = describe(new)
    ours, theirs = [], []
    for pid in new:
        cmd = commands.get(pid, "(exited before it could be described)")
        ppid = after[pid][0]
        root = attributable(cmd, roots)
        if root:
            ours.append(f"pid {pid} (ppid {ppid}) {cmd}")
        elif ppid == 1:
            # Measured: a leaked child's parent is gone, so it is adopted by init. That is the
            # signature of an orphan and it is named even when the command line says nothing.
            ours.append(f"pid {pid} (ppid 1 — orphaned) {cmd}")
        else:
            theirs.append(f"pid {pid} (ppid {ppid}) {cmd}")
    return (1 if ours else 0), ours, theirs


def emit(ours: list[str], theirs: list[str], repo: str | None, verbose: bool = False) -> None:
    if ours:
        print(f"✗ [orphan_canary] {len(ours)} process(es) outlived the run that started them:")
        for line in ours:
            print(f"    {line[:240]}")
        print(
            f"  Something a test spawned is still running{f' out of {repo}' if repo else ''}. A leak that"
        )
        print(
            "  only shows up as a LATER hang is a leak with no signal of its own — an orphan holding a"
        )
        print(
            "  build lock blocks every subsequent run with no output at all. Wrap the child in a guard"
        )
        print("  that reaps it on a panic (checks/check_no_unreaped_spawn.py names the pattern).")
    if theirs:
        # ONE line, no pids unless asked. Another program on a shared machine starts processes during
        # the window -- measured 2026-10-03 through local_ci.sh: three of another repo's
        # `sccache`/`rustc` plus a Karabiner daemon, on a GREEN run of two trivial steps. Per-pid
        # output would put twenty lines of other people's work on screen after every step of every
        # gate, which is how a canary earns being switched off. The attributable half above is the
        # one that is loud, because that is the one that means something.
        print(
            f"⚠ [orphan_canary] {len(theirs)} process(es) started elsewhere on this machine during"
        )
        print(
            "  the step — reported, not failed (a canary that failed on those is one nobody leaves"
        )
        print(
            "  on). --verbose names them; lib/orphan_canary.py attributes by checkout path and ppid."
        )
        for line in theirs if verbose else []:
            print(f"    {line}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # The command is split off at the FIRST `--` BY HAND rather than with argparse.REMAINDER.
    # REMAINDER starts collecting at the first bare word, so `wrap SNAP --repo R -- CMD` handed the
    # canary `--repo` as the command to run and it tried to exec it: measured through this CLI,
    # `bounded_run: cannot start --repo`. A gate's own argument parsing must not depend on where
    # argparse decides a positional ended.
    command: list[str] = []
    if "--" in argv:
        at = argv.index("--")
        argv, command = argv[:at], argv[at + 1 :]
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=("take", "since", "wrap"))
    ap.add_argument("snapshot", nargs="?", help="path to a snapshot file")
    ap.add_argument("--repo", default=None, help="repo root, for attribution (default: cwd)")
    ap.add_argument("--verbose", action="store_true", help="name every unattributable process")
    args = ap.parse_args(argv)
    repo = os.path.realpath(args.repo or os.getcwd())

    if args.mode == "take":
        if not args.snapshot:
            print("orphan_canary take needs a snapshot path", file=sys.stderr)
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(args.snapshot)), exist_ok=True)
        with open(args.snapshot, "w", encoding="utf-8") as handle:
            json.dump({"v": SNAPSHOT_VERSION, "pids": snapshot()}, handle)
        return 0

    if args.mode == "wrap":
        if not command:
            print("orphan_canary wrap needs a command: ... wrap SNAP -- CMD", file=sys.stderr)
            return 2
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import bounded_run

        # The BEFORE table is KEPT, not re-read and not re-taken. The first version of `wrap` took
        # the snapshot, wrote it, ran the step, and then called `report(snapshot(), ...)` — handing
        # the function the AFTER table as its `before`. Every orphan was then present in both
        # tables, so `new` was empty and the canary reported a clean run while the leaked server
        # was still running. Measured on this machine, 2026-10-03: `sleep 408` alive with ppid 1,
        # canary exit 0. A canary's whole job is to be right about the case it exists for, and the
        # only way to know it is, is to check that case.
        before = snapshot()
        if args.snapshot:
            os.makedirs(os.path.dirname(os.path.abspath(args.snapshot)), exist_ok=True)
            with open(args.snapshot, "w", encoding="utf-8") as handle:
                json.dump({"v": SNAPSHOT_VERSION, "pids": before}, handle)
        # DEVNULL, not inherit: a leaked child holding an inherited pipe's write end blocks the
        # caller to EOF, so `wrap` would hang FOREVER on the very orphan it exists to report.
        # Measured 2026-10-03: 1201 s, inside the test asserting the report.
        code = bounded_run.run(
            command,
            bounded_run.DEFAULT_TIMEOUT,
            5,
            command[0],
            output=subprocess.DEVNULL,
        )
        verdict, ours, theirs = report(before, roots_for(repo), repo)
        emit(ours, theirs, repo, args.verbose)
        # The step's own verdict first: a red step is the news, and a canary red on top of it must
        # not read as the canary being the failure.
        return code or verdict

    if not args.snapshot:
        print("orphan_canary since needs a snapshot path", file=sys.stderr)
        return 2
    try:
        with open(args.snapshot, encoding="utf-8") as handle:
            before = json.load(handle).get("pids", {})
    except (OSError, ValueError) as exc:
        print(f"orphan_canary: cannot read {args.snapshot}: {exc}", file=sys.stderr)
        return 2
    verdict, ours, theirs = report(before, roots_for(repo), repo)
    emit(ours, theirs, repo, args.verbose)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
