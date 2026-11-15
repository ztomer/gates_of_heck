"""The ONE acquire path for goh's locks: a wait only in the order lib/lock_order.tsv names.

    (lib/lock_order.sh runs main() from the module beside it: never a from-HEAD re-exec, which
    would not hand the shell's fd to the flock reliably, nor pay an export per acquisition)
    lock_order check NAME     # exit 0, or 78 refusing, naming the held lock and the table
    lock_order held NAME PID  # GOH_HELD_LOCKS with NAME@PID added (the shell's export)
    lock_order flock FD NAME [--nb] [--shared]   # check, then take the kernel flock on FD

THE INCIDENT (GOH.LOCKORDER, 2026-11-15). goh v0.122.0's ship held the land lock and its release
step waited 1800 s for the load slot; the slot was held by a land run inside tools/load.py,
which was queued on the land lock. Ship: land, then load. Land: load, then land. Each waited on the
other until the release timed out. Nothing named the cycle; it read as a slow machine.

THE RULE (lockdep's). Every lock has a rank in ONE table. A process may WAIT on a lock only when its
rank is above every lock held by it or by an ancestor; an out-of-order wait is refused before it
starts, deterministically (contention or not), naming both locks and the table. A try-lock that
never waits is always allowed. Held locks travel to children in GOH_HELD_LOCKS (`name@pid,...`): a
child waiting on a lock its ancestor holds a later one of is the deadlock's shape whoever runs it.
An entry whose pid is dead is ignored (an inherited variable from a gone holder guards nothing).
A lock the table marks re-entrant (its own nesting rule lets a holder's child through) is not
refused when it is the one already held; anything else held at or above its rank is.

checks/check_lock_order.py is the static half: every acquisition site in the tree goes through
this module or lib/lock_order.sh, and the held-while-acquiring graph sorts topologically.
Pinned by tests/test_lock_order.py.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

TABLE = Path(__file__).resolve().with_name("lock_order.tsv")
ENV = "GOH_HELD_LOCKS"
EXIT_ORDER = 78  # EX_CONFIG: the invocation nests locks against the table


@dataclass(frozen=True)
class Lock:
    name: str
    rank: int
    reentrant: bool
    files: tuple[str, ...]
    what: str


class OrderError(RuntimeError):
    """A wait the table forbids: the message names both locks and the table."""


def table(path: Path = TABLE) -> dict[str, Lock]:
    """The lock table, by name."""
    out: dict[str, Lock] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, rank, reentrant, files, what = line.split("\t")
        out[name] = Lock(name, int(rank), reentrant == "yes", tuple(files.split(",")), what)
    return out


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def held(environ: dict[str, str] | None = None) -> list[tuple[str, int]]:
    """The locks this process and its ancestors hold, (name, pid), the dead holders dropped."""
    raw = (os.environ if environ is None else environ).get(ENV, "")
    out = []
    for part in raw.split(","):
        name, _, pid = part.strip().partition("@")
        if name and pid.isdigit() and _alive(int(pid)):
            out.append((name, int(pid)))
    return out


def refusal(name: str, environ: dict[str, str] | None = None) -> str | None:
    """Why waiting on NAME now would break the order, or None when it may wait."""
    locks = table()
    if name not in locks:
        return f"lock order: {name!r} is not in {TABLE} -- every goh lock has a rank there"
    want = locks[name]
    for other, pid in held(environ):
        have = locks.get(other)
        if have is None:
            continue
        if other == name and want.reentrant:
            continue
        if have.rank >= want.rank:
            return (f"lock order: refusing to wait on '{name}' (rank {want.rank}: {want.what}) "
                    f"while '{other}' (rank {have.rank}, held by pid {pid}) is held -- a holder of "
                    f"'{name}' may be waiting on '{other}', which is the deadlock of 2026-11-15; "
                    f"take '{name}' first, or release '{other}' (the order: {TABLE})")  # fmt: skip
    return None


def check(name: str) -> None:
    """Raise OrderError when waiting on NAME now would break the table's order."""
    if why := refusal(name):
        raise OrderError(why)


def with_held(name: str, pid: int, environ: dict[str, str] | None = None) -> str:
    """GOH_HELD_LOCKS with NAME@PID added: the value a holder exports to its children."""
    raw = (os.environ if environ is None else environ).get(ENV, "")
    parts = [p for p in raw.split(",") if p.strip() and p.strip() != f"{name}@{pid}"]
    return ",".join([*parts, f"{name}@{pid}"])


@contextlib.contextmanager
def holding(name: str) -> Iterator[None]:
    """Mark NAME as held by this process for the block: children inherit it in GOH_HELD_LOCKS."""
    before = os.environ.get(ENV)
    os.environ[ENV] = with_held(name, os.getpid())
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = before


def flock(fd: object, name: str, *, wait: bool = True, shared: bool = False) -> None:
    """The kernel flock on FD for lock NAME. WAIT: check the order first (refused deterministically,
    whether or not the lock is free); not WAIT: a try-lock that raises OSError when it is taken."""
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if not wait:
        if name not in table():
            raise OrderError(f"lock order: {name!r} is not in {TABLE}")
        fcntl.flock(fd, mode | fcntl.LOCK_NB)  # type: ignore[arg-type]
        return
    check(name)
    fcntl.flock(fd, mode)  # type: ignore[arg-type]


def unlock(fd: object) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[arg-type]


def main(argv: list[str]) -> int:
    if argv[:1] == ["check"] and len(argv) == 2:
        if why := refusal(argv[1]):
            print(why, file=sys.stderr)
            return EXIT_ORDER
        return 0
    if argv[:1] == ["held"] and len(argv) == 3:
        print(with_held(argv[1], int(argv[2])))
        return 0
    if argv[:1] == ["flock"] and len(argv) >= 3:
        fd, name, flags = int(argv[1]), argv[2], set(argv[3:])
        try:
            flock(fd, name, wait="--nb" not in flags, shared="--shared" in flags)
        except OrderError as why:
            print(why, file=sys.stderr)
            return EXIT_ORDER
        except OSError:
            return 1  # a try-lock that found it taken
        return 0
    print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
    return 2
