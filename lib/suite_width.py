"""suite_width.py -- how many xdist workers a NARROWED suite run takes (GOH.PYHARNESS).

WHY. tools/pytest.sh started every run at the live share's ceiling (12 workers): one 12-test file
took 5.6 s in pytest, 10 CPU seconds of it workers starting, against 2.5 s with none (2026-11-25).
The suite's own table (lib/durations.py) says what the chosen tests cost, so a run narrowed to
files or node ids takes one worker per WORKER_S of it, and none at SERIAL_S or under.

WHAT IT DOES NOT ANSWER (None: the ceiling, as before) is every run it cannot cost: no file named,
a directory, an option that is not in the sets below (one could take a path as its value, or widen
the run), an empty table, an unreadable one, a land's run (its verdict record is the ceiling's).
A caller's own `-n` is after the runner's and wins. A test function the table has not timed -- the
one being written -- counts UNMEASURED_S, never 0; a new parametrization of a timed one is seen at
its next top-level run. `--collect-only` starts no worker whatever it collects.

Invoked by lib/suite_preflight.py (tools/pytest.sh). Pinned by tests/test_suite_width.py.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

SERIAL_S = 2.0  # a worker's start costs 0.4 s of wall and 0.6 s of CPU, at the least
WORKER_S = 1.5
UNMEASURED_S = 0.5
LAND = ("GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE")
FLAGS = frozenset(("-q", "-qq", "-v", "-vv", "-x", "-s", "-l", "--lf", "--ff", "--pdb"))
PREFIXES = ("-r", "--tb=", "--maxfail=", "--durations=", "-k=", "-m=", "--color=")
VALUED = frozenset(("-k", "-m"))  # each narrows the run, whatever its value names
COLLECT = frozenset(("--collect-only", "--co"))


def targets(args: Sequence[str], root: Path) -> list[str] | None:
    """The test files and node ids of a narrowed run, as node ids; None when it is no such run."""
    out: list[str] = []
    rest = iter(args)
    for a in rest:
        if a in VALUED:
            next(rest, None)
        elif a in FLAGS or a.startswith(PREFIXES):
            continue
        else:
            path, sep, node = a.partition("::")
            real = (root / path).resolve()
            if not real.is_file() or not real.is_relative_to(root) or real.suffix != ".py":
                return None
            out.append(f"{real.relative_to(root).as_posix()}{sep}{node}")
    return out or None


def _holds(target: str, node: str) -> bool:
    return node == target or node.startswith((target + "::", target + "["))


def cost(named: Sequence[str], table: Mapping[str, float], root: Path) -> float:
    """The seconds `named` takes: each timed test once, and each function nothing timed."""
    from lib.fast_tests import functions

    total = sum(s for node, s in table.items() if any(_holds(t, node) for t in named))
    timed = {node.split("[", 1)[0] for node in table}
    new: set[str] = set()
    for rel in {t.split("::", 1)[0] for t in named}:
        for fn in functions((root / rel).read_text(encoding="utf-8", errors="replace")):
            node = f"{rel}::{fn}"
            if node not in timed and any(
                _holds(t, node) or t.startswith(node + "[") for t in named
            ):
                new.add(node)
    return total + UNMEASURED_S * len(new)


def _serial(args: Sequence[str]) -> bool:
    """Does the caller ask for no worker itself (`-n 0`, its last -n)? Every `goh land --check` job
    does, and each started a share pool for nobody."""
    said = [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-n"]
    return said[-1:] == ["0"]


def width(args: Sequence[str], root: Path, environ: Mapping[str, str] | None = None) -> int | None:
    env = os.environ if environ is None else environ
    if COLLECT & set(args) or _serial(args):
        return 0
    if any(env.get(k) for k in LAND):
        return None
    named = targets(args, root)
    if named is None:
        return None
    from lib.fast_tests import load

    table = load()
    if not table:
        return None
    seconds = cost(named, table, root)
    return 0 if seconds <= SERIAL_S else math.ceil(seconds / WORKER_S)
