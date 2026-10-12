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

import fcntl
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SHARED = Path.home() / ".cargo" / "advisory-db"


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


@contextmanager
def held(fetch: bool) -> Iterator[Path]:
    """The database, locked for the block: exclusive to refresh (or seed), shared to read."""
    db = path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{db}.goh-lock", "a", encoding="utf-8") as lock:
        exclusive = fetch or not db.exists()
        fcntl.flock(lock, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        if not db.exists():
            _seed(db)
        yield db


def audit_args(db: Path, fetch: bool) -> list[str]:
    """What an audit adds so it reads `db`, and refreshes it only when asked."""
    return ["--db", str(db), *([] if fetch else ["--no-fetch"])]


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
    with held(fetch) as db:
        return subprocess.run(
            [*argv[3:], *audit_args(db, fetch)], check=False, stdin=subprocess.DEVNULL
        ).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
