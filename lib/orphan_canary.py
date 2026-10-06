#!/usr/bin/env python3
"""Report the processes a test step LEFT RUNNING — the orphans, which are silent by construction.

    lib/orphan_canary.py wrap [--repo DIR] [--log FILE] [--snapshot FILE]
                              [--timeout N] [--grace N] [--label TEXT] [--verbose] -- CMD

EXIT. 0 nothing survived · 1 the step left something running · the STEP's own code otherwise (a red
step is the news, and a canary red on top of it must not read as the canary being the failure) ·
2 usage error.

WHY, and why it is separate from the ceiling
--------------------------------------------
`lib/bounded_run.py` bounds a step and sweeps what the step itself spawned. It cannot help with the
case that actually cost a day: the test **finished**, so the step exited 0, and the server the test
leaked outlived it. media_server, 2026-10-03: nine live orphans, each holding the cargo build lock,
every later `cargo test` blocked with no output at all, and the run that could have reported the
leak was the run that had been killed. A leak that only shows up as a *later* hang is a leak with
no signal of its own, so this measures one.

HOW: ANCESTRY, EXACTLY. `lib/bounded_run.py` samples the descendant set of the step at the instant
it exits; anything in that set still alive afterwards is a leak, with no inference.

**What this replaced, and why it was wrong.** The first version diffed the whole process table
either side of the step and treated "a new process whose ppid is 1" as an orphan — true in isolation,
and a machine-global heuristic in practice. Measured 2026-10-03 under this repo's own parallel
suite: every run reported the OTHER workers' deliberately-leaked processes, and three end-to-end
tests went red for a reason that had nothing to do with what they measured. Two gates on one machine
cross-report for the same reason; so does `--full` racing a pre-commit. A pid's parentage after
reparenting is a rumour; "was under this step a moment ago" is a fact.

A PATH IS AN ANNOTATION, NOT EVIDENCE. A survivor whose command line names this repo (the checkout,
a `target/` under it, `$CARGO_TARGET_DIR`) is labelled `[under <root>]`, which is what makes a leaked
test binary recognisable in the report -- but only processes from the step's own group are judged
at all. (This header used to promise the opposite: that a repo-named process NOT observed under the
step was reported too. `judge` never did that, and must not: under GOH_CI_JOBS two concurrent steps
of one repo would each report the other's live `cargo test` as its own leak.) The house's runner
machinery (`orphan_canary.py`, `bounded_run.py`) is excluded: a concurrent gate run is not a leak.
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
import json
import os
import subprocess
import sys
from dataclasses import dataclass

SNAPSHOT_VERSION = 1

# "The step passed and left something running." Its own code, above bounded_run's 124, because a
# caller has to tell this apart from a step that simply failed — the two have opposite fixes (write
# a guard vs. fix the test) and the same exit status would send someone to the wrong one. 125 is the
# conventional "reserved, do not use" value, which is exactly right for a condition a step did not
# choose.
LEAK_EXIT = 125

# A path inside this repo makes a process ours even when we did not observe it under the step.
# Also what a leaked cargo-built binary looks like: `…/crates/x-rs/target/debug/archive_torznab`.
# A process started by something else on the machine is NOT reported at all: on a shared box that is
# every build on the host, and a canary that cries wolf is one nobody leaves switched on.
MACHINERY = ("orphan_canary.py", "bounded_run.py")


@dataclass(frozen=True)
class Verdict:
    """(code, attributable lines, unattributable lines). Attributable lines are the failures."""

    code: int
    ours: list[str]
    theirs: list[str]


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
    """{pid: command line} for the named pids, one `ps` for all of them.

    FULL command lines: attribution matches a PATH, and truncating at 160 chars cut the path off
    before the token that proves it -- measured: a leaked `…/target/debug/archive_torznab` under
    pytest's tmp dir (a 190-char path) was attributed but then displayed without its own name, so
    the report could not name what it found. Truncation is a DISPLAY decision, made at print time.
    """
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


def judge(pids: list[int], roots: list[str]) -> Verdict:
    """Every pid handed in is OURS, by proof rather than by inference.

    `pids` comes from `bounded_run`'s sample of the step's own PROCESS GROUP at the instant it exited
    (see `group_members`). Membership of that group is not a guess about whose process it was: a
    non-interactive shell does not put a background job in a new group, and the step was started with
    `start_new_session`, so anything still there was started by this step. Measured on the incident's
    shape: `64886 1 64885 /bin/sleep 422` -- reparented to init, pgid intact, named.

    That is what replaced the machine-global "new process with ppid 1" rule, which cross-reported
    every concurrent gate on the box and made three end-to-end tests red for a reason unrelated to
    what they measured. It is also why there is no second, "probably ours" category here: a verdict
    built on two kinds of evidence is a verdict whose wrong answers nobody can tell apart.
    """
    # A pid that has already exited is not a survivor and cannot be a leak. `bounded_run` filters
    # these with `kill -0` before calling; doing it here too means a caller that does not cannot
    # manufacture a finding out of a process that finished. Measured: the transient `ps` children a
    # concurrent canary makes are reported as "(exited before it could be described)" and were
    # counted as leaks.
    live = []
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        live.append(pid)
    keys = [str(pid) for pid in live]
    commands = describe(keys)
    ours = []
    for pid in keys:
        cmd = commands.get(pid, "(exited before it could be described)")
        if any(tool in cmd for tool in MACHINERY):
            continue  # a concurrent gate run, not a leak this step made
        where = attributable(cmd, roots)
        ours.append(f"pid {pid} {cmd}" + (f"   [under {where}]" if where else ""))
    return Verdict(code=1 if ours else 0, ours=ours, theirs=[])


def emit(ours: list[str], repo: str | None, verbose: bool = False) -> None:
    """The report. Every line here is a process this step left running."""
    if ours:
        print(f"✗ [orphan_canary] {len(ours)} process(es) outlived the run that started them:")
        for line in ours:
            print(f"    {line[:240]}")
        print(
            f"  Something a test spawned is still running{f' out of {repo}' if repo else ''}. A leak"
        )
        print(
            "  that only shows up as a LATER hang is a leak with no signal of its own — an orphan"
        )
        print("  holding a build lock blocks every subsequent run with no output at all. Wrap the")
        print(
            "  child in a guard that reaps it on a panic (checks/check_no_unreaped_spawn.py names the"
        )
        print("  pattern).")


def wrap(args, command: list[str]) -> int:
    """Snapshot the step's descendants, run it under a ceiling, report what it left."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bounded_run

    outcome = bounded_run.run_step(
        command,
        args.timeout,
        args.grace,
        args.label or command[0],
        output=args.log or bounded_run.DEVNULL_SENTINEL,
    )
    survivors = outcome.survivors
    roots = roots_for(args.repo or os.getcwd())
    verdict = judge(survivors, roots)
    if args.snapshot and survivors:
        # Kept as evidence for a red push: which pids, and when. `push_gate.sh` keeps a red run's
        # log and deletes a green one's, so a canary finding has to travel with the step's output
        # rather than only on the scrollback.
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.snapshot)), exist_ok=True)
            with open(args.snapshot, "w", encoding="utf-8") as handle:
                json.dump(
                    {"v": SNAPSHOT_VERSION, "step": command, "survivors": survivors},
                    handle,
                    indent=2,
                )
        except OSError as exc:
            print(f"orphan_canary: cannot write {args.snapshot}: {exc}", file=sys.stderr)
    emit(verdict.ours, args.repo or os.getcwd(), args.verbose)
    # The step's own verdict first: a red step is the news, and the leak is still printed beside it.
    # Only a GREEN step's leak gets its own code, because that is the case with no other news.
    if outcome.code:
        return outcome.code
    return LEAK_EXIT if verdict.ours else 0


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
    ap.add_argument("mode", choices=("wrap",))
    ap.add_argument(
        "--snapshot", default=None, help="where to record the surviving pids, for a red push"
    )
    ap.add_argument("--repo", default=None, help="repo root, for attribution (default: cwd)")
    ap.add_argument("--log", default=None, help="file for the step's own output")
    ap.add_argument("--timeout", type=int, default=900, help="ceiling for the step, seconds")
    ap.add_argument("--grace", type=int, default=5, help="TERM->KILL grace on expiry, seconds")
    ap.add_argument("--label", default="", help="what to call the step in messages")
    ap.add_argument("--verbose", action="store_true", help="name every unattributable process")
    args = ap.parse_args(argv)
    if not command:
        print("orphan_canary wrap needs a command: ... wrap [opts] -- CMD", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print(f"orphan_canary: --timeout must be positive (got {args.timeout})", file=sys.stderr)
        return 2
    if not os.path.isfile(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bounded_run.py")
    ):
        print(
            "orphan_canary: lib/bounded_run.py is missing — a step would run unbounded",
            file=sys.stderr,
        )
        return 2
    return wrap(args, command)


if __name__ == "__main__":
    sys.exit(main())
