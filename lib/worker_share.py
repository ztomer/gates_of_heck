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
the machine. Admission's rule is unchanged (D-0009 kept count admission); this sizes width only.

THE SHARE IS LIVE (GOH.SHAREGROW, 2026-11-17 21:58). It was taken once at admission and never moved:
the SELECTAUDIT land's coverage step ran the goh suite at `-n 4` for 11+ minutes on a box at load
7.9 of 16 cores, the other gate and its sibling steps long gone. Now the runners that CAN follow the
count do, both ways: `live()` is cores // the gates admitted NOW (+1 for a run holding no slot,
GOH_SHARE_OWN) // the steps of each enclosing gates/local_ci.sh running NOW (GOH_SHARE_STEPS, a
file per scheduler). The goh suite starts xdist at its CEILING and every worker takes a TOKEN
(`Pool`, flock'd files <pool>/tok-<i>, i < live share) before each test and gives it back after:
a gate leaving grows the run within one test per worker, a gate admitted shrinks it the same way,
and a worker without a token waits on a 0.2 s poll that costs nothing. Tokens beat re-sizing xdist
(it has no live resize) and beat a per-test re-count in every worker (the count is re-read once per
GOH_SHARE_PERIOD seconds per worker, default 2). `cargo test` follows it per TEST BINARY: the
admission names this file as cargo's runner (`run`), which hands each libtest binary the live
RUST_TEST_THREADS -- unless the repo configures a runner of its own, which is never overridden.
THE BOUND: the tokens of every gate sum to at most cores within one test of any admission (the
incident's rule, now kept live); a runner with a fixed width (swift, xcodebuild, cargo's build
jobs, nextest) keeps its share from its start, as before.

THE CHANNEL. The share reaches every runner a gate starts through the environment the runners
themselves read, exported once at admission (lib/gate_slot.sh, lib/admission.py), so a consumer's
own `cargo test` or `make` is bounded without a word in its gate script: `RUNNER_ENV` below. The
runners that read no variable (`swift test`, `xcodebuild`) take the share as a flag from the goh
layer that starts them (`share()` here, `goh_gate_workers` in lib/gate_slot.sh). A gate that runs
N steps at once (gates/local_ci.sh GOH_CI_JOBS) gives each step `--split N` of its share.

THE SHAPE of a machine-sized pool -- xdist's auto count, swift's parallel flag with no worker
count, a build runner's job flag with no count, the coreutils and sysctl core counts -- is
MACHINE_SIZED below, ONE definition read twice: goh's own scripts by the ratchet in
tests/test_worker_share.py, every consumer's gate scripts by checks/check_worker_share.py, which
`structural.sh --full` runs in every repo goh gates (GOH.POOLSHARE2).
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
import contextlib
import json
import os
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

import gate_slot
import lock_order

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


MACHINE_SIZED = re.compile(r"""(?x)
    (?:-n|--numprocesses)[\s=]+(?:auto|logical)\b         # pytest-xdist: every core
  | --parallel(?![\w-])(?!.*--num-workers)                # swift test: a worker per core
  | \b(?:g?make|cargo|ninja|cmake)\b[^|;&]*?              # a build runner's job flag
    (?<![\w-])-j(?![\w$"'{-])(?!\s*[\d$"'])               #   with no bound at all
  | (?<![\w-])nproc(?![\w-])                              # the machine's count, by name
  | hw\.(?:ncpu|logicalcpu|physicalcpu|activecpu)
  | _NPROCESSORS_(?:ONLN|CONF)
""")


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


OWN_ENV = "GOH_SHARE_OWN"  # 1: this gate holds no slot and is one more beside them; 0: it holds one
STEPS_ENV = "GOH_SHARE_STEPS"  # ':'-joined files, each a gates/local_ci.sh's count of running steps
POOL_ENV = "GOH_SHARE_POOL"  # the token directory of the run whose workers take tokens
CEIL_ENV = "GOH_SHARE_CEIL"  # that run's worker count: the most tokens it can use
SLOTS_ENV = "GOH_SHARE_SLOTS"  # the slot directory, resolved where the run started
SHARE_CORES_ENV = "GOH_SHARE_CORES"  # the cores, read where the run started
PACKED_ENV = "GATES_OF_HECK_SUITE_SHARE"  # the goh suite's pack of them (tests/suite_home.py)
PERIOD_ENV = "GOH_SHARE_PERIOD"  # seconds between a worker's re-counts (default 2)
PIN_ENV = "GOH_SHARE_RUST_PINNED"  # 1: RUST_TEST_THREADS is the repo's own, never re-sized
TOKEN_LOCK = "share_token"  # lib/lock_order.tsv: try-locks only, never a wait


def own(directory: str, environ: dict[str, str] | None = None) -> int:
    """1 when this process's gate holds no slot in `directory` (it counts itself), else 0."""
    env = os.environ if environ is None else environ
    raw = env.get(OWN_ENV, "").strip()
    if raw in ("0", "1"):
        return int(raw)
    return 0 if gate_slot.nested(directory) is not None else 1


def steps(environ: dict[str, str] | None = None) -> int:
    """The product of the running-step counts of every enclosing scheduler, at least 1 each: a file
    that cannot be read (its scheduler is gone) divides by nothing."""
    env = os.environ if environ is None else environ
    total = 1
    for path in filter(None, env.get(STEPS_ENV, "").split(":")):
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read().strip()
        except OSError:
            continue
        total *= max(1, int(raw)) if raw.isdigit() else 1
    return total


def live(environ: dict[str, str] | None = None) -> int:
    """This runner's share NOW: cores // the gates admitted now // the sibling steps running now."""
    env = os.environ if environ is None else environ
    directory = os.path.realpath(slot_dir(env))
    gates = admitted(directory) + own(directory, env)
    return divide(divide(cores(env), gates), steps(env))


class Pool:
    """A run's live tokens: <directory>/tok-<i>, i < its live share (at most `ceiling`). A worker
    holds one exclusive flock per test; a share that shrinks leaves the high tokens untaken, so the
    run narrows as its tests end, and one that grows opens them at the next test."""

    def __init__(
        self,
        directory: str,
        ceiling: int,
        width: Callable[[], int] = live,
        period: float = 2.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.directory, self.ceiling, self.width = directory, max(1, ceiling), width
        self.period, self.clock = period, clock
        self._cap, self._at = 1, None  # type: int, float | None
        os.makedirs(directory, exist_ok=True)

    def cap(self) -> int:
        now = self.clock()
        if self._at is None or now - self._at >= self.period:
            with contextlib.suppress(OSError, ValueError):  # unreadable: the last count stands
                self._cap = max(1, min(self.ceiling, self.width()))
            self._at = now
        return self._cap

    def take(self, worker: int, wait: bool = True, poll: float = 0.2) -> int | None:
        """An fd holding one token (close it to give it back), trying `worker`'s own index first;
        None when every token is held and not `wait`."""
        while True:
            cap = self.cap()
            for k in range(cap):
                fd = os.open(os.path.join(self.directory, f"tok-{(worker + k) % cap}"),
                             os.O_RDWR | os.O_CREAT, 0o644)  # fmt: skip
                try:
                    lock_order.flock(fd, TOKEN_LOCK, wait=False)
                except OSError:
                    os.close(fd)
                    continue
                return fd
            if not wait:
                return None
            time.sleep(poll)


class Tokens:
    """One xdist worker's side (tests/conftest.py's hookwrappers, in a worker of tools/pytest.sh's
    run): collection and each test's setup, call and teardown run holding one token."""

    def __init__(self, pool: Pool, worker: int) -> None:
        self.pool, self.worker = pool, worker
        self.held: int | None = None

    def take(self) -> None:
        self.held = self.pool.take(self.worker)

    def give(self) -> None:
        if self.held is not None:
            os.close(self.held)
        self.held = None


def tokens_from_env(environ: dict[str, str] | None = None) -> Tokens | None:
    """The tokens of this xdist worker, or None (no pool: a serial run, a controller, a consumer's
    pytest). Read from GOH_SHARE_* or, under the goh suite's scrub, from their pack (PACKED_ENV);
    either is removed, so nothing a test starts takes a token."""
    env = os.environ if environ is None else environ
    worker = env.get("PYTEST_XDIST_WORKER", "")
    if not worker.startswith("gw") or not worker[2:].isdigit():
        return None  # the controller keeps them: its workers start after they are read
    packed = env.pop(PACKED_ENV, "")
    src = (
        json.loads(packed)
        if packed
        else {k: v for k, v in env.items() if k.startswith("GOH_SHARE_")}
    )
    env.pop(POOL_ENV, None)
    directory, ceil, period = src.get(POOL_ENV, ""), src.get(CEIL_ENV, ""), src.get(PERIOD_ENV, "")
    if not directory:
        return None
    count = {  # what live() reads, as tools/pytest.sh resolved it under the caller's HOME
        "GOH_GATE_SLOT_DIR": src.get(SLOTS_ENV, "") or slot_dir(dict(env)),
        CORES_ENV: src.get(SHARE_CORES_ENV, ""),
        OWN_ENV: src.get(OWN_ENV, "1"),
        STEPS_ENV: src.get(STEPS_ENV, ""),
    }
    pool = Pool(
        directory,
        int(ceil) if ceil.isdigit() else 1,
        width=lambda: live(count),
        period=float(period) if period.replace(".", "", 1).isdigit() else 2.0,
    )
    return Tokens(pool, int(worker[2:]))


def cargo_runner_var() -> str | None:
    """cargo's runner variable for this host (house targets only), None elsewhere."""
    machine = os.uname().machine
    arch = {"arm64": "AARCH64", "aarch64": "AARCH64", "x86_64": "X86_64"}.get(machine)
    if arch is None:
        return None
    host = "APPLE_DARWIN" if sys.platform == "darwin" else "UNKNOWN_LINUX_GNU"
    return f"CARGO_TARGET_{arch}_{host}_RUNNER"


def repo_runner(cwd: str, environ: dict[str, str]) -> str | None:
    """Where a runner of the repo's (or the user's) own is configured, else None: any runner
    variable already set, or a `runner` in any target table of cargo's config files from `cwd`
    (gates/_cargo_config.py, the one reading of them). A file that cannot be parsed counts as one:
    a runner is never overridden on a guess."""
    named = sorted(k for k in environ if k.startswith("CARGO_TARGET_") and k.endswith("_RUNNER"))
    if named:
        return named[0]
    gates = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gates")
    if gates not in sys.path:
        sys.path.append(gates)
    import _cargo_config  # gates/, reached only when a runner is being named

    home = environ.get("CARGO_HOME") or os.path.expanduser("~/.cargo")
    for f in _cargo_config._config_files(Path(cwd), Path(home)):
        try:
            targets = _cargo_config._load(f).get("target", {})
        except ValueError:
            return str(f)
        if any(isinstance(t, dict) and "runner" in t for t in targets.values()):
            return str(f)
    return None


def run(argv: list[str], environ: dict[str, str] | None = None) -> int:
    """cargo's runner: exec the binary, a libtest one (cargo keeps those in deps/) on the live
    share -- divided by GOH_SHARE_DIV when that many binaries run at once (gates/_cov_run_tests.py
    split_share). A RUST_TEST_THREADS that is neither the share nor its split (a repo's own,
    GOH_SHARE_RUST_PINNED) is never touched."""
    env = dict(os.environ if environ is None else environ)
    if not argv:
        print("worker_share run: no program", file=sys.stderr)
        return 2
    div, threads = env.get("GOH_SHARE_DIV", ""), env.get("RUST_TEST_THREADS", "")
    ours = div.isdigit() or (threads != "" and threads == env.get(ENV, ""))
    if env.get(PIN_ENV) != "1" and ours and os.path.basename(os.path.dirname(argv[0])) == "deps":
        try:
            now = live(env)
        except (OSError, ValueError):
            now = 0
        if now:
            env["RUST_TEST_THREADS"] = str(divide(now, int(div) if div.isdigit() else 1))
    os.execvpe(argv[0], argv, env)
    return 127  # not reached


def slot_dir(environ: dict[str, str]) -> str:
    """lib/gate_slot.sh's directory rule: GOH_GATE_SLOT_DIR, else the machine's."""
    return environ.get("GOH_GATE_SLOT_DIR", "").strip() or gate_slot.default_dir()


def runner_env(
    workers: int, environ: dict[str, str] | None = None, mine: int | None = None
) -> dict[str, str]:
    """The variables that hold every runner to `workers`. One already set LOWER stays: a repo
    saying RUST_TEST_THREADS=1 says its tests cannot run at once; a share never widens a runner.
    With `mine` (0: the gate holds a slot, 1: it does not), what the live share needs too: the
    count's GOH_SHARE_OWN and, where the repo names no runner of its own, cargo's runner."""
    env = os.environ if environ is None else environ
    out = {ENV: str(workers)}
    for name in RUNNER_ENV[1:]:
        have = env.get(name, "").strip()
        out[name] = have if have.isdigit() and 0 < int(have) < workers else str(workers)
    have = env.get("RUST_TEST_THREADS", "").strip()
    if env.get(PIN_ENV) == "1" or (
        have.isdigit() and 0 < int(have) <= workers and have != env.get(ENV, "")
    ):  # the repo's own count (not a share it inherited): the live share never re-sizes it
        out[PIN_ENV] = "1"
    if mine is not None:
        out[OWN_ENV] = str(mine)
        var, me = cargo_runner_var(), os.path.abspath(__file__)
        py = env.get("GOH_PY", "") or sys.executable
        if var and not repo_runner(os.getcwd(), dict(env)) and " " not in py + me:
            out[var] = f"{py} -S {me} run"
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
    p = sub.add_parser(
        "pool", help="KEY=VALUE: a token pool at PATH for a run of up to CEIL workers"
    )
    p.add_argument("--path", required=True)
    p.add_argument("--ceiling", type=int, required=True)
    sub.add_parser("live", help="this runner's share now")
    p = sub.add_parser("run", help="cargo's runner: exec a test binary on the live share")
    p.add_argument("program", nargs=argparse.REMAINDER)
    if argv[:1] == ["run"]:  # the binary's own flags are never this parser's
        return run(argv[1:][1:] if argv[1:2] == ["--"] else argv[1:])
    args = ap.parse_args(argv)
    env = dict(os.environ)
    try:
        if args.cmd == "live":
            print(live(env))
            return 0
        directory = os.path.realpath(getattr(args, "dir", "") or slot_dir(env))
        if args.cmd == "pool":
            os.makedirs(args.path, exist_ok=True)
            print(f"{POOL_ENV}={args.path}")
            print(f"{CEIL_ENV}={divide(min(args.ceiling, cores(env)), 1)}")
            print(f"{OWN_ENV}={own(directory, env)}")
            print(f"{SLOTS_ENV}={directory}")
            print(f"{SHARE_CORES_ENV}={cores(env)}")
            return 0
        if args.admitted:
            got, mine = divide(cores(env), admitted(directory)), 0
        else:
            got, mine = share(args.dir or None, env), None
        workers = divide(got, args.split)
    except ValueError as why:
        print(f"worker_share: {why}", file=sys.stderr)
        return 2
    if args.cmd == "share":
        print(workers)
    else:
        if mine is None:
            mine = own(directory, env)
        for key, value in runner_env(workers, env, mine).items():
            print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
