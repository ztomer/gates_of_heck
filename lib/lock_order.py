"""The ONE acquire path for goh's locks: a wait only in the order lib/lock_order.tsv names.

    (lib/lock_order.sh runs main() from the module beside it: never a from-HEAD re-exec, which
    would not hand the shell's fd to the flock reliably, nor pay an export per acquisition)
    lock_order check NAME [--where P]     # exit 0, or 78 refusing, naming the held lock, the table
    lock_order held NAME PID [--where P]  # GOH_HELD_LOCKS with NAME@PID+BORN:KEY added (exported)
    lock_order flock FD NAME [--nb] [--shared] [--where P]  # check, then the kernel flock on FD

THE INCIDENT (GOH.LOCKORDER, 2026-11-15). goh v0.122.0's ship held the land lock and its release
step waited 1800 s for the load slot; the slot was held by a land run inside tools/load.py,
which was queued on the land lock. Ship: land, then load. Land: load, then land. Each waited on the
other until the release timed out. Nothing named the cycle; it read as a slow machine.

THE RULE (lockdep's). Every lock has a rank in ONE table. A process may WAIT on a lock only when its
rank is above every lock held by it or by an ancestor; an out-of-order wait is refused before it
starts, deterministically (contention or not), naming both locks and the table. A try-lock that
never waits is always allowed. Held locks travel to children in GOH_HELD_LOCKS
(`name@pid+born,...`): a child waiting on a lock its ancestor holds a later one of is the
deadlock's shape whoever runs it.
An entry whose holder is gone is ignored (an inherited variable from a gone holder guards nothing),
and the holder is its pid AND its birth (`born`, lib/pid_owner.py's start time, GOH.STRAYSIGNAL3):
a pid alone read whatever process the number was handed to as the holder still running. An entry
an older goh wrote, with no birth, is never a live holder.
A lock the table marks re-entrant (its own nesting rule lets a holder's child through) is not
refused when it is the one already held; anything else held at or above its rank is.

SCOPE (GOH.LOCKNEST). A held mark carries the repository it was taken in (`name@pid:key`, the key a
hash of the git common dir: every worktree of one repository shares it; `where` names the lock's
file or the repository, else the working directory). A lock the table scopes `repo` -- one per
repository -- is ordered only against the marks of ITS repository, and the marks of no known one:
`.loop`'s commit gate held the machine's gate slot, and its test's fixture ship was refused waiting
on the fixture repo's own land lock, which no holder of that slot could be waiting for. A machine
lock stays ordered against every mark.

THE SLOT TAKEN FOR A gate.sh (GOH.LOCKNEST). structural.sh --full takes the gate slot FOR its parent
`tools/gate.sh` (lib/gate_slot.py, "HELD FOR THE CALLER"), and no variable it exports reaches that
gate.sh's later children. So a wait ranked below the slot also asks lib/gate_slot.py whether a slot
in GOH_GATE_SLOT_DIR is recorded to an ancestor of this process: that gate.sh holds it for them too.

checks/check_lock_order.py is the static half: every acquisition site in the tree goes through
this module or lib/lock_order.sh, and the held-while-acquiring graph sorts topologically.
Pinned by tests/test_lock_order.py.

`lock_order.py` / `lock_order.sh` / `lock_order.tsv`: The ONE acquire path for goh's locks
(GOH.LOCKORDER, after the 2026-11-15 ship/land deadlock): the table ranks every lock, and a WAIT is
refused (exit 78, naming both locks and the table) unless its rank is above every lock this process
or an ancestor holds (`GOH_HELD_LOCKS`, `name@pid:key`, dead holders ignored); a try-lock is never
refused; a re-entrant lock held by an ancestor is its own nesting rule's. A `repo`-scoped lock (the
table's scope column) is ordered only against marks of its own repository (the key: its git common
dir) or of none, and a wait ranked below the gate slot also sees a slot recorded to an ancestor
(the one structural.sh takes for a `tools/gate.sh`) Python `flock`/`check`/`holding`, shell
`goh_lock_take`/`goh_lock_check`/`goh_lock_mark`. Pinned by `test_lock_order_deadlock.py`,
`test_check_lock_order.py`.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pid_owner  # the lib directory, on the path from the line above

TABLE = Path(__file__).resolve().with_name("lock_order.tsv")  # load-read: lib/lock_order.tsv
ENV = "GOH_HELD_LOCKS"
EXIT_ORDER = 78  # EX_CONFIG: the invocation nests locks against the table


@dataclass(frozen=True)
class Lock:
    name: str
    rank: int
    reentrant: bool
    per_repo: bool
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
        name, rank, reentrant, scope, files, what = line.split("\t")
        if scope not in ("repo", "machine"):
            raise ValueError(f"{path}: '{name}' has scope {scope!r}, not 'repo' or 'machine'")
        out[name] = Lock(name, int(rank), reentrant == "yes", scope == "repo",
                         tuple(files.split(",")), what)  # fmt: skip
    return out


def born_of(pid: int) -> str:
    """The birth a mark carries after its pid (`sec.usec`), or "" when the pid has none to read."""
    found = pid_owner.stamp(pid)
    return "" if found is None else f"{found.born[0]}.{found.born[1]}"


def _alive(pid: int, born: str) -> bool:
    """The mark's holder is still the process that took the lock (a mark without a birth: no)."""
    sec, dot, usec = born.partition(".")
    if not (dot and sec.isdigit() and usec.isdigit()):
        return False
    return pid_owner.alive(pid_owner.Stamp(pid, (int(sec), int(usec)), 0))


def _holder(part: str) -> str:
    """`name@pid` of a mark, whatever birth and key follow it."""
    name, at, rest = part.strip().partition("@")
    return name + at + rest.partition(":")[0].partition("+")[0]


def _common_dir(start: Path) -> Path | None:
    """The git common dir of the repository holding START, found by walking up (no git spawned)."""
    here = start if start.is_dir() else start.parent
    for d in (here, *here.parents):
        dot = d / ".git"
        if dot.is_dir():
            return dot
        if dot.is_file():
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
            if not text.startswith("gitdir:"):
                return None
            gitdir = (d / text.removeprefix("gitdir:").strip()).resolve()
            common = gitdir / "commondir"
            if common.is_file():
                return (gitdir / common.read_text(encoding="utf-8").strip()).resolve()
            return gitdir
    return None


def repo_key(where: str | os.PathLike[str] | None = None) -> str:
    """The scope a mark records: a hash of WHERE's git common dir (the working directory when
    None), or "" outside any repository."""
    try:
        start = Path(os.getcwd() if where is None else where).resolve()
        common = _common_dir(start)
    except OSError:
        return ""
    return hashlib.sha1(str(common.resolve()).encode()).hexdigest()[:12] if common else ""


def held_marks(environ: dict[str, str] | None = None) -> list[tuple[str, int, str]]:
    """The locks this process and its ancestors hold, (name, pid, repo key or ""), the dead holders
    dropped."""
    raw = (os.environ if environ is None else environ).get(ENV, "")
    out = []
    for part in raw.split(","):
        name, _, rest = part.strip().partition("@")
        holder, _, key = rest.partition(":")
        pid, _, born = holder.partition("+")
        if name and pid.isdigit() and _alive(int(pid), born):
            out.append((name, int(pid), key))
    return out


def held(environ: dict[str, str] | None = None) -> list[tuple[str, int]]:
    """The locks this process and its ancestors hold, (name, pid), the dead holders dropped."""
    return [(name, pid) for name, pid, _ in held_marks(environ)]


def ancestors() -> set[int]:
    """This process's ancestry (its parent up to pid 1), from one `ps` listing."""
    import subprocess  # only a wait below the gate slot asks

    out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
                         check=False, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    parent: dict[int, int] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
    seen: set[int] = set()
    pid = os.getppid()
    while pid > 0 and pid not in seen:
        seen.add(pid)
        pid = parent.get(pid, 0)
    return seen


def _slot_above(environ: dict[str, str] | None) -> tuple[str, int, str] | None:
    """The gate slot an ancestor holds by record (the one taken FOR a gate.sh), as a mark."""
    try:
        import gate_slot  # here: it imports this module
    except ImportError:
        return None
    raw = (environ or {}).get("GOH_GATE_SLOT_DIR") or os.environ.get("GOH_GATE_SLOT_DIR")
    found = gate_slot.recorded_above(os.path.realpath(raw or gate_slot.default_dir()))
    return None if found is None else ("gate_slot", found[0], found[1])


def refusal(
    name: str, environ: dict[str, str] | None = None, where: str | os.PathLike[str] | None = None
) -> str | None:
    """Why waiting on NAME (the lock of WHERE's repository, for a `repo` lock) now would break the
    order, or None when it may wait."""
    locks = table()
    if name not in locks:
        return f"lock order: {name!r} is not in {TABLE} -- every goh lock has a rank there"
    want = locks[name]
    marks = held_marks(environ)
    slot = locks.get("gate_slot")
    below_slot = slot is not None and want.rank < slot.rank
    if below_slot and all(m[0] != "gate_slot" for m in marks) and (above := _slot_above(environ)):
        marks.append(above)
    mine = repo_key(where) if want.per_repo else ""
    for other, pid, key in marks:
        have = locks.get(other)
        if have is None:
            continue
        if mine and key and key != mine:
            continue  # another repository's lock: never one a holder of this one waits behind
        if other == name and want.reentrant:
            continue
        if have.rank >= want.rank:
            return (f"lock order: refusing to wait on '{name}' (rank {want.rank}: {want.what}) "
                    f"while '{other}' (rank {have.rank}, held by pid {pid}) is held -- a holder of "
                    f"'{name}' may be waiting on '{other}', which is the deadlock of 2026-11-15; "
                    f"take '{name}' first, or release '{other}' (the order: {TABLE})")  # fmt: skip
    return None


def check(name: str, where: str | os.PathLike[str] | None = None) -> None:
    """Raise OrderError when waiting on NAME now would break the table's order."""
    if why := refusal(name, where=where):
        raise OrderError(why)


def with_held(
    name: str,
    pid: int,
    environ: dict[str, str] | None = None,
    where: str | os.PathLike[str] | None = None,
) -> str:
    """GOH_HELD_LOCKS with NAME@PID+BORN:KEY added (BORN: PID's start time; KEY: WHERE's repository,
    "" outside one): the value a holder exports to its children."""
    raw = (os.environ if environ is None else environ).get(ENV, "")
    me = f"{name}@{pid}"
    parts = [p for p in raw.split(",") if p.strip() and _holder(p) != me]
    born = born_of(pid)
    mark = f"{me}+{born}" if born else me
    key = repo_key(where)
    return ",".join([*parts, f"{mark}:{key}" if key else mark])


@contextlib.contextmanager
def holding(name: str, where: str | os.PathLike[str] | None = None) -> Iterator[None]:
    """Mark NAME as held by this process for the block: children inherit it in GOH_HELD_LOCKS."""
    before = os.environ.get(ENV)
    os.environ[ENV] = with_held(name, os.getpid(), where=where)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = before


def flock(
    fd: object,
    name: str,
    *,
    wait: bool = True,
    shared: bool = False,
    where: str | os.PathLike[str] | None = None,
) -> None:
    """The kernel flock on FD for lock NAME. WAIT: check the order first (refused deterministically,
    whether or not the lock is free); not WAIT: a try-lock that raises OSError when it is taken."""
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if not wait:
        if name not in table():
            raise OrderError(f"lock order: {name!r} is not in {TABLE}")
        fcntl.flock(fd, mode | fcntl.LOCK_NB)  # type: ignore[arg-type]
        return
    check(name, where)
    fcntl.flock(fd, mode)  # type: ignore[arg-type]


def unlock(fd: object) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)  # type: ignore[arg-type]


def main(argv: list[str]) -> int:
    where: str | None = None
    if "--where" in argv:
        at = argv.index("--where")
        if at + 1 >= len(argv):
            print("lock_order: --where needs a path", file=sys.stderr)
            return 2
        where = argv[at + 1] or None
        argv = argv[:at] + argv[at + 2 :]
    if argv[:1] == ["check"] and len(argv) == 2:
        if why := refusal(argv[1], where=where):
            print(why, file=sys.stderr)
            return EXIT_ORDER
        return 0
    if argv[:1] == ["held"] and len(argv) == 3:
        print(with_held(argv[1], int(argv[2]), where=where))
        return 0
    if argv[:1] == ["flock"] and len(argv) >= 3:
        fd, name, flags = int(argv[1]), argv[2], set(argv[3:])
        try:
            flock(fd, name, wait="--nb" not in flags, shared="--shared" in flags, where=where)
        except OrderError as why:
            print(why, file=sys.stderr)
            return EXIT_ORDER
        except OSError:
            return 1  # a try-lock that found it taken
        return 0
    print("\n".join(__doc__.splitlines()[2:5]), file=sys.stderr)
    return 2
