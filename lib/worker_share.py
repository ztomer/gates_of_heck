#!/usr/bin/env python3
"""THE WORKER SHARE (GOH.POOLSHARE): how wide one admitted gate may run.

    lib/worker_share.py share [--dir D] [--split N] [--admitted]   # this gate's worker budget
    lib/worker_share.py env   [--dir D] [--split N] [--admitted]   # KEY=VALUE, what runners read

THE INCIDENT (2026-11-17 ~20:40Z). The load reached 213-237 on 16 cores with 0% idle: a Swift app's
push gate (13 xctest workers from swift's unbounded parallel test run), a goh full suite (~32 pytest
workers), two land pre-steps, a goh ci_hook in slot 1 and a cargo test ran at once, EACH sizing its
workers to all 16 cores -- ~220 runnable threads, every worker getting about a fifth of a core.
Admission (the gate slots, D-0009) bounds how MANY gates run; nothing bounded how WIDE each ran.

THE RULE. A gate's share is the machine's cores divided by the gates admitted beside it, itself
included, at least 1: `cores // admitted`. Admitted means a held slot of EITHER lane (the long
slots and the commit gate's short slots both run a suite). A run that holds no slot (a land's
pre-step running tools/pytest.sh) counts itself as one more gate, so it too takes a share and not
the machine. The share is taken at admission and does not change while the gate runs: a runner's
width is fixed at its start. Admission's rule is unchanged (D-0009 kept count admission); this sizes
width only.

THE CHANNEL. The share reaches every runner a gate starts through the environment the runners
themselves read, exported once at admission (lib/gate_slot.sh, lib/admission.py), so a consumer's
own `cargo test` or `make` is bounded without a word in its gate script: `RUNNER_ENV` below. The
runners that read no variable (`swift test`, `xcodebuild`) take the share as a flag from the goh
layer that starts them (`share()` here, `goh_gate_workers` in lib/gate_slot.sh). A gate that runs
N steps at once (gates/local_ci.sh GOH_CI_JOBS) gives each step `--split N` of its share.

The ratchet that refuses a NEW machine-sized pool in a gate script -- an os.cpu_count pool,
xdist's auto count, swift's parallel flag with no worker count, a make or cargo job flag with no
count, the coreutils and sysctl core counts -- is tests/test_worker_share.py (MACHINE_SIZED).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import os
import sys

import gate_slot

ENV = "GOH_GATE_WORKERS"  # this gate's share, exported at admission
CORES_ENV = "GOH_GATE_CORES"  # the seam: the cores to share (tests pin 16 on any box)

# What each runner reads, so a runner a consumer starts by hand is bounded too. Only variables that
# NARROW a runner whose default is the machine: never MAKEFLAGS or CMAKE_BUILD_PARALLEL_LEVEL, whose
# runner is SERIAL unset (make is -j1), so exporting a count would widen a Makefile never written to
# run in parallel (probed on GNU make 3.81, 2026-11-17: MAKEFLAGS=-j8 starts a jobserver). A gate
# script that runs make says `make -j"$(goh_workers)"`; a bare `-j` is refused by the ratchet.
RUNNER_ENV = (
    ENV,
    "PYTEST_XDIST_AUTO_NUM_WORKERS",  # pytest-xdist's auto and logical counts
    "CARGO_BUILD_JOBS",  # cargo's --jobs: rustc processes at once
    "RUST_TEST_THREADS",  # libtest: tests at once inside one test binary
    "NEXTEST_TEST_THREADS",  # cargo nextest's --test-threads
)


def cores(environ: dict[str, str] | None = None) -> int:
    """The machine's online cores (sysconf, as lib/load_sources.py counts them); GOH_GATE_CORES is
    the seam a test pins."""
    env = os.environ if environ is None else environ
    seam = env.get(CORES_ENV, "").strip()
    if seam:
        if not seam.isdigit() or int(seam) < 1:
            raise ValueError(f"{CORES_ENV} must be a positive integer of cores (got {seam!r})")
        return int(seam)
    return max(1, os.sysconf("SC_NPROCESSORS_ONLN") or 1)


def divide(total: int, gates: int) -> int:
    """`total` cores among `gates` gates: at least one worker each, never zero."""
    return max(1, total // max(1, gates))


def admitted(directory: str) -> int:
    """How many gates hold a slot in `directory` now, of either lane."""
    return sum(1 for path in gate_slot.slot_files(directory) if gate_slot.held(path))


def counted(directory: str, environ: dict[str, str] | None = None) -> int:
    """The share counted from the slots: this process's own gate is among the held slots when its
    ancestry holds one (by GOH_GATE_SLOT or by record), else it is one more beside them."""
    gates = admitted(directory)
    if gate_slot.nested(directory) is None:
        gates += 1
    return divide(cores(environ), gates)


def share(directory: str | None = None, environ: dict[str, str] | None = None) -> int:
    """This gate's worker budget: the admission's GOH_GATE_WORKERS, else counted from the slots."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return counted(os.path.realpath(directory or slot_dir(env)), env)


def slot_dir(environ: dict[str, str]) -> str:
    """lib/gate_slot.sh's directory rule: GOH_GATE_SLOT_DIR, else the machine's."""
    return environ.get("GOH_GATE_SLOT_DIR", "").strip() or gate_slot.default_dir()


def runner_env(workers: int, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The variables that hold every runner to `workers`. One already set LOWER stays: a repo
    saying RUST_TEST_THREADS=1 says its tests cannot run at once; a share never widens a runner."""
    env = os.environ if environ is None else environ
    out = {ENV: str(workers)}
    for name in RUNNER_ENV[1:]:
        have = env.get(name, "").strip()
        out[name] = have if have.isdigit() and 0 < int(have) < workers else str(workers)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("share", "env"):
        p = sub.add_parser(name)
        p.add_argument("--dir", default="")
        p.add_argument("--split", type=int, default=1, help="steps run at once that share it")
        p.add_argument(
            "--admitted", action="store_true",
            help="the caller was just admitted: its slot is among the held ones; nothing inherited",
        )  # fmt: skip
    args = ap.parse_args(argv)
    env = dict(os.environ)
    try:
        if args.admitted:
            got = divide(cores(env), admitted(os.path.realpath(args.dir or slot_dir(env))))
        else:
            got = share(args.dir or None, env)
        workers = divide(got, args.split)
    except ValueError as why:
        print(f"worker_share: {why}", file=sys.stderr)
        return 2
    if args.cmd == "share":
        print(workers)
    else:
        for key, value in runner_env(workers, env).items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
