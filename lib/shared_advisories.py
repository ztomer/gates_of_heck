#!/usr/bin/env python3
"""shared_advisories.py -- the estate's ONE ruling per advisory every consumer inherits through
one upstream (GOH.SHAREDADVISORY; docs/shared-advisories.md).

gates/shared_advisories.toml holds each entry: the advisory id, the advised crate, the BLOCKING
crate whose newest release still requires it, why it stands and when it re-opens. An entry covers
a lock only where the blocker still reaches the advised crate through its dependencies: a lock
that carries the crate some other way is not covered, and one whose blocker dropped it is named.

    python3 lib/shared_advisories.py ignores LOCK   # `--ignore ID` args rust_gate.sh adds
    python3 lib/shared_advisories.py dropped LOCK   # exit 1: an entry's blocker no longer needs it

GOH_SHARED_ADVISORIES names another file (a test's fixture).

The estate's ONE ruling per advisory every consumer inherits through one upstream
(GOH.SHAREDADVISORY, `docs/shared-advisories.md`): reads `gates/shared_advisories.toml` (id, crate,
blocker, reason, re-open condition); `ignores LOCK` names the `--ignore ID` args for the entries
whose blocker still reaches the crate in that lock (`lib/advisory_db.py run -- cargo audit` adds
them itself, for every goh audit and a consumer's own step through it, GOH.AUDITONCE), `dropped
LOCK` exits 1 when a lock's blocker no longer does (the re-open condition met).
`checks/check_audit_ignores.py --shared` judges an entry stale when it fires in no estate lock;
`scripts/estate_audit.py` honours the same entries.
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

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "gates" / "shared_advisories.toml"
FIELDS = ("id", "crate", "blocker", "reason", "reopen")


@dataclass(frozen=True)
class Entry:
    id: str
    crate: str
    blocker: str
    reason: str
    reopen: str


def path() -> Path:
    return Path(os.environ.get("GOH_SHARED_ADVISORIES") or FILE)


def load_text(text: str, where: str = "shared advisories") -> list[Entry]:
    out = []
    for i, raw in enumerate(tomllib.loads(text).get("advisory", [])):
        missing = [f for f in FIELDS if not str(raw.get(f, "")).strip()]
        if missing:
            raise ValueError(f"{where}: entry {i + 1} lacks {', '.join(missing)}")
        out.append(Entry(**{f: str(raw[f]) for f in FIELDS}))
    return out


def load(file: Path | None = None) -> list[Entry]:
    f = file or path()
    return load_text(f.read_text(encoding="utf-8"), str(f)) if f.is_file() else []


def graph(lock_text: str) -> dict[str, set[str]]:
    """name -> names it depends on, every version of a name folded together."""
    g: dict[str, set[str]] = {}
    for p in tomllib.loads(lock_text).get("package", []):
        g.setdefault(p["name"], set()).update(d.split()[0] for d in p.get("dependencies", []))
    return g


def _reaches(g: dict[str, set[str]], start: str, goal: str) -> bool:
    seen, todo = set(), list(g.get(start, ()))
    while todo:
        n = todo.pop()
        if n == goal:
            return True
        if n not in seen:
            seen.add(n)
            todo.extend(g.get(n, ()))
    return False


def applies(e: Entry, lock_text: str) -> bool:
    return _reaches(graph(lock_text), e.blocker, e.crate)


def dropped(e: Entry, lock_text: str) -> bool:
    """The re-open condition, met in this lock: it carries the advised crate and the blocker, and
    the blocker no longer reaches it."""
    g = graph(lock_text)
    return e.crate in g and e.blocker in g and not _reaches(g, e.blocker, e.crate)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("ignores", "dropped"):
        print(__doc__, file=sys.stderr)
        return 2
    text = Path(argv[1]).read_text(encoding="utf-8")
    bad = 0
    for e in load():
        if argv[0] == "ignores" and applies(e, text):
            print(f"--ignore {e.id}")
        elif argv[0] == "dropped" and dropped(e, text):
            bad += 1
            print(
                f"{e.id}: {e.blocker} no longer depends on {e.crate} ({e.reopen}): re-open the "
                f"ruling in {path()}",
                file=sys.stderr,
            )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
