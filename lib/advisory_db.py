"""The advisory database every goh `cargo audit` reads, and the lock that keeps a refresh from
tearing a read.

`cargo audit --no-fetch` reads `~/.cargo/advisory-db` while any other `cargo audit` on the machine
may be refreshing it, and a refresh rewrites the checkout file by file. On 2026-10-12 a land's
push gate ran its own `cargo audit` (a refresh) beside its test suite, whose fixture gates audited
with `--no-fetch`, and two of three crates failed `parse error: unexpected start of advisory: ""`
on a file caught half-written. The same race is open between any two repos pushing at once.

So goh reads and refreshes ITS OWN copy (`GOH_ADVISORY_DB`, default `~/.cache/goh/advisory-db`),
which nothing outside goh writes, and every goh audit holds a flock on `<db>.goh-lock` while it
runs: shared to read (`--no-fetch`), exclusive to refresh. Readers run together; a refresh waits
for them and they wait for it. A missing copy is seeded from `~/.cargo/advisory-db` (a git clone
reads objects and refs, which a concurrent fetch only adds to and swaps atomically) or, failing
that, fetched by the audit itself, under the exclusive lock either way.

    python3 lib/advisory_db.py run --fetch|--no-fetch -- cargo audit --file Cargo.lock ...

EVERY AUDIT IS BOUNDED, by its own short ceiling (GOH.AUDITSPIN, 2026-11-14). cargo-audit 0.23.0
spun at 100% CPU for 10.5 and 3.3 minutes in two push gates, its output stopped at "Scanning
Cargo.lock": the yanked check, which asks the crates.io sparse index for every locked crate.
tame-index 0.26.3 (`AsyncRemoteSparseIndex::exec_request`) retries a connect error in a loop with
no backoff, and the FIRST crate's request runs with no deadline at all, so a name lookup that fails
fast (the resolver refused every lookup around an account logoff) is a busy loop in getaddrinfo
that never ends: `sample` showed every tokio worker in getaddrinfo -> nw_path_evaluator ->
necp_client_action and fs_usage 620,761 reads of /etc/hosts in 20 s. Reproduced on demand with
`HTTPS_PROXY=http://no-such-host.invalid:3128 cargo audit --deny yanked` (never finishes). The
step's own ceiling was the gate's (900 s wall, 900 s x cores of CPU), so the spin ran for minutes.

So (1) a FETCHING audit first asks the lookup cargo-audit would spin on -- the proxy's host when
one is set, else the index's -- and refuses, naming it, when it does not resolve; an offline audit
(`--no-fetch`) reads the cached index and asks nothing. (2) Every audit runs in its own process
group under a CPU ceiling (`GOH_AUDIT_CPU`, default 30 s; a 412-crate scan with its fetch costs
under 1 s) and a wall ceiling (`GOH_AUDIT_WALL`, default 360 s: cargo-audit may wait up to 300 s
for cargo's package lock, which costs no CPU), and a stop names the stall.

EVERY AUDIT HONOURS goh's SHARED RULINGS (GOH.AUDITONCE, 2026-11-17). A consumer's own audit step
(GOH_CI_STEPS) ran through this wrapper, not through rust_gate.sh, so it never saw
gates/shared_advisories.toml and refused the v0.130.0 pin pushes on RUSTSEC-2026-0543 (chrono via
rmcp). So `run -- cargo audit` adds `--ignore ID` for each ruling that covers the lock it reads
(`--file`/`-f`, else ./Cargo.lock) exactly as the gate does (lib/shared_advisories.py: only where
the blocker still reaches the advised crate), and refuses, naming it, a ruling whose blocker
dropped the crate.

The advisory database every goh `cargo audit` reads -- goh's own copy (`GOH_ADVISORY_DB`, default
`~/.cache/goh/advisory-db`, seeded from `~/.cargo/advisory-db`), which nothing outside goh writes
-- held under a flock on `<db>.goh-lock`: shared to read (`--no-fetch`), exclusive to refresh. A
refresh beside a read tore the read ("unexpected start of advisory", 2026-10-12). `run
--fetch|--no-fetch -- cargo audit ...` for the shell gate and a consumer's own step, which adds
goh's shared rulings for the lock it reads (`--file`/`-f`, else `./Cargo.lock`) and refuses one
whose blocker dropped the crate (GOH.AUDITONCE); `held()` and `audit_args()` for the Python
callers.
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

import itertools
import os
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # the one acquire path (lib/lock_order.py, GOH.LOCKORDER), imported as `X` or as `lib.X`
    import lock_order
    import shared_advisories
except ImportError:
    from lib import lock_order, shared_advisories

SHARED = Path.home() / ".cargo" / "advisory-db"
UPSTREAM = "https://github.com/RustSec/advisory-db.git"
INDEX_HOST = "index.crates.io"
DEFAULT_CPU_S = 30
DEFAULT_WALL_S = 360
LOOKUP_S = 15
TIMEOUT_EXIT = 124
PROXIES = ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")


def path() -> Path:
    """The database goh audits against."""
    raw = os.environ.get("GOH_ADVISORY_DB")
    if raw:
        return Path(raw)
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache) / "goh" / "advisory-db"


def _seed(db: Path) -> None:
    """A first copy from cargo-audit's own, when there is one; else the audit's fetch makes it."""
    if (SHARED / ".git").exists() or (SHARED / "HEAD").exists():
        subprocess.run(
            ["git", "clone", "-q", "--local", str(SHARED), str(db)],
            check=False,
            capture_output=True,
            stdin=subprocess.DEVNULL,
        )
        _upstream(db)


def _upstream(db: Path) -> None:
    """`origin` is the upstream: cargo-audit fetches through `origin`, and a copy cloned from the
    local seed kept the seed there, so every refresh re-read it however stale it was
    (GOH.SHAREDADVISORY: two days and 18 advisories behind, RUSTSEC-2026-0543 among them)."""
    if not (db / ".git").exists():
        return
    git = ["git", "-C", str(db), "remote"]
    url = subprocess.run(
        [*git, "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    if url != UPSTREAM:
        subprocess.run(
            [*git, "set-url", "origin", UPSTREAM],
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )


@contextmanager
def held(fetch: bool) -> Iterator[Path]:
    """The database, locked for the block: exclusive to refresh (or seed), shared to read."""
    db = path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{db}.goh-lock", "a", encoding="utf-8") as lock:
        exclusive = fetch or not db.exists()
        lock_order.flock(lock, "advisory_db", shared=not exclusive)
        if not db.exists():
            _seed(db)
        elif exclusive:
            _upstream(db)
        yield db


def audit_args(db: Path, fetch: bool) -> list[str]:
    """What an audit adds so it reads `db`, and refreshes it only when asked -- or when there is no
    copy to read at all (nothing to seed from: a fresh hosted runner), which `held` already locked
    exclusively for this first fetch (run 42950563400)."""
    return ["--db", str(db), *([] if fetch or not db.exists() else ["--no-fetch"])]


def _ceiling(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def lookup_host(env: dict[str, str] | None = None) -> str:
    """The name cargo-audit's HTTP client resolves first: the proxy's when one is set."""
    env = os.environ if env is None else env
    for name in PROXIES:
        raw = env.get(name, "").strip()
        if raw:
            host = raw.split("://", 1)[-1].split("@", 1)[-1].split("/", 1)[0]
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
            if host:
                return host.strip("[]")
    return env.get("GOH_AUDIT_INDEX_HOST") or INDEX_HOST


def unresolved(env: dict[str, str] | None = None) -> str | None:
    """None when the lookup a fetching audit depends on answers; else the refusal, in words."""
    host = lookup_host(env)
    answer: list[object] = []

    def ask() -> None:
        try:
            answer.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except OSError as exc:
            answer.append(exc)

    worker = threading.Thread(target=ask, daemon=True)
    worker.start()
    worker.join(LOOKUP_S)
    got = answer[0] if answer else TimeoutError(f"no answer in {LOOKUP_S} s")
    if isinstance(got, list) and got:
        return None
    return (
        f"cargo audit not run: {host} does not resolve ({got}) -- cargo-audit's yanked check would"
        " retry that lookup with no backoff and no deadline, a 100%-CPU spin that never ends"
        " (GOH.AUDITSPIN, tame-index 0.26.3). Check `dscacheutil -q host -a name"
        f" {host}`; GOH_DEPS_OFFLINE=1 judges against the cached database and index instead."
    )


def _stall(expired: str, cpu_s: int, wall_s: int) -> str:
    if "(CPU budget)" in expired:
        what = f"burned {cpu_s} s of CPU (a whole scan costs under 1 s; GOH_AUDIT_CPU)"
    else:
        what = f"ran {wall_s} s without finishing (GOH_AUDIT_WALL)"
    return (
        f"cargo audit stopped: it {what} -- the known spin in its yanked check, tame-index retrying"
        " a failed lookup of the crates.io index with no backoff and no deadline (GOH.AUDITSPIN)."
        f" Check `dscacheutil -q host -a name {lookup_host()}`; GOH_DEPS_OFFLINE=1 judges against"
        " the cached database and index instead."
    )


def bounded(
    argv: list[str],
    fetch: bool,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run one `cargo audit` under the audit's own ceilings, in its own process group.

    A refusal or a stop is exit 124 (1 for a refusal) with the reason as stderr's LAST line, which
    is the line the callers quote. `capture=False` inherits stdout and stderr (the gate step)."""
    if fetch and (refusal := unresolved(env)) is not None:
        if not capture:
            print(refusal, file=sys.stderr)
        return subprocess.CompletedProcess(argv, 1, "", refusal + "\n" if capture else "")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import killtree
        import pgid_cpu
    finally:
        del sys.path[0]
    cpu_s, wall_s = (
        _ceiling("GOH_AUDIT_CPU", DEFAULT_CPU_S),
        _ceiling("GOH_AUDIT_WALL", DEFAULT_WALL_S),
    )
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(  # files, never pipes: a full pipe would stall the child we time
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out if capture else None,
            stderr=err if capture else None,
            start_new_session=True,
        )
        pgid = killtree.session_pgid(proc)
        expired = pgid_cpu.wait_for_exit(
            proc, pgid, wall_s, " ".join(argv[:2]), cpu_timeout=cpu_s, stall=None
        )
        code = proc.returncode
        if expired is not None:
            killtree.stop_group(pgid, proc=proc)
            proc.wait()
            code = TIMEOUT_EXIT
        if not capture:
            if expired is not None:
                print(_stall(expired, cpu_s, wall_s), file=sys.stderr)
            return subprocess.CompletedProcess(argv, code)
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode(errors="replace")
        stderr = err.read().decode(errors="replace")
    if expired is not None:
        stderr += (
            ("" if not stderr or stderr.endswith("\n") else "\n")
            + _stall(expired, cpu_s, wall_s)
            + "\n"
        )
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def _audited_lock(argv: list[str]) -> Path | None:
    """The Cargo.lock a `cargo [+toolchain] audit ...` argv reads; None for any other command."""
    rest = argv[1:]
    if rest and rest[0].startswith("+"):
        rest = rest[1:]
    if not argv or Path(argv[0]).name != "cargo" or not rest or rest[0] != "audit":
        return None
    lock = "Cargo.lock"
    for i, a in enumerate(rest[1:], start=1):
        if a in ("--file", "-f") and i + 1 < len(rest):
            lock = rest[i + 1]
        elif a.startswith("--file="):
            lock = a.split("=", 1)[1]
    return Path(lock)


def shared_rulings(argv: list[str]) -> tuple[list[str], list[str]]:
    """(`--ignore ID` args the shared rulings add for this audit's lock, the rulings it re-opens):
    the same answer lib/shared_advisories.py gives rust_gate.sh. An ID the argv already ignores is
    not added twice."""
    lock = _audited_lock(argv)
    if lock is None or not lock.is_file():
        return [], []
    text = lock.read_text(encoding="utf-8")
    have = {b for a, b in itertools.pairwise(argv) if a == "--ignore"}
    have |= {a.split("=", 1)[1] for a in argv if a.startswith("--ignore=")}
    add: list[str] = []
    reopened: list[str] = []
    for e in shared_advisories.load():
        if shared_advisories.dropped(e, text):
            reopened.append(
                f"{e.id}: {e.blocker} no longer depends on {e.crate} in {lock} ({e.reopen}):"
                f" re-open the ruling in {shared_advisories.path()}"
            )
        elif e.id not in have and shared_advisories.applies(e, text):
            add += ["--ignore", e.id]
    return add, reopened


def main(argv: list[str]) -> int:
    if (
        len(argv) < 4
        or argv[0] != "run"
        or argv[1] not in ("--fetch", "--no-fetch")
        or argv[2] != "--"
    ):
        print("usage: advisory_db.py run --fetch|--no-fetch -- cargo audit ...", file=sys.stderr)
        return 2
    fetch = argv[1] == "--fetch"
    shared, reopened = shared_rulings(argv[3:])
    if reopened:
        print("\n".join(reopened), file=sys.stderr)
        return 1
    with held(fetch) as db:
        return bounded(
            [*argv[3:], *shared, *audit_args(db, fetch)], fetch, capture=False
        ).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
