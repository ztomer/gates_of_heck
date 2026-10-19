"""The estate is every repo `install.sh` installed into, not one directory (BACKLOG 6.14).

`~/Projects` WAS the estate: a directory, walked three levels deep. But `install.sh` wires goh into
whatever repository it is pointed at, and one of the estate's consumers lives OUTSIDE that
directory -- `~/.claude/skills` carries the house hooks -- so the audit sweep, the table in
BACKLOG 3.3 and `scripts/estate_audit.py` never read it. It was red on the stdin gate with 35
undeclared calls, and nothing in the house could say so, because nothing knew it was there.

So the estate is a REGISTRY: `install.sh` records the toplevel it installed into, one line per
repo, in goh's own state directory beside `estate_audit.json`. Every estate sweep reads that
registry UNIONED with the directory it walks, and a registered path that is GONE is named: a
consumer that leaves the estate must be a decision somebody made, not a line quietly dropped.

THREE WAYS THIS CAN LIE, each with a test in `tests/test_estate_registry.py`:

* a repo installed BEFORE the registry existed -- seeded once from the `~/Projects` walk plus
  `~/.claude/skills` (`scripts/estate_registry.py seed`), so the first run is not a smaller
  estate than the machine;
* a repo MOVED on disk -- the registry is keyed by PATH, so a moved repo is a gone path AND a new
  one: both are named, and nobody guesses which repository "went missing";
* a scratch clone registered by a test -- every test uses its OWN state directory, so the
  machine's real registry is never written by a test.

THE FILE is one absolute path per line, beside the audit's state file. `GOH_ESTATE_STATE` names
that state file, so it names the registry's directory too: a test that names its own state file
gets its own registry, and the machine's registry is never a test's business.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

REGISTRY_NAME = "installed_repos"
SKIP_DIRS = {"node_modules", "target", ".build", ".venv", "venv"}
REPO_DEPTH = 3  # ~/Projects/<repo>, ~/Projects/<group>/<repo>: servers/media_server, games/*


def state_file() -> Path:
    """Where the estate audit records what it found: `GOH_ESTATE_STATE`, else
    `$XDG_STATE_HOME/goh/estate_audit.json`, else `~/.local/state/goh/estate_audit.json`."""
    env = os.environ.get("GOH_ESTATE_STATE")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base).expanduser() / "goh" / "estate_audit.json"


def registry_path(state: Path | None = None) -> Path:
    """The registry, BESIDE the audit's state file: one directory, one file apart. A caller that
    names its own state file therefore gets its own registry -- which is how a test never writes
    the machine's."""
    s = Path(state) if state is not None else state_file()
    return s.expanduser().resolve().parent / REGISTRY_NAME


def read(path: Path | None = None) -> tuple[list[Path], list[str]]:
    """(the registered repos, the lines that could not be read).

    One absolute path per line. A blank line and a `#` comment are ignored; a line that is not an
    absolute path is a PROBLEM, named by the caller, never dropped in silence: a registry that
    silently loses an entry is how a consumer leaves the estate without anybody deciding that.
    """
    p = Path(path) if path is not None else registry_path()
    if not p.exists():
        return [], []
    repos, bad = [], []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        q = Path(line).expanduser()
        if not q.is_absolute():
            bad.append(line)
            continue
        if q not in repos:
            repos.append(q)
    return repos, bad


def record(path: Path, registry: Path | None = None) -> bool:
    """Record the repo's toplevel; False when it was already there.

    Append-only and one line per repo: the registry is a list of where the estate IS, not a
    history of how it got there.
    """
    p = Path(registry) if registry is not None else registry_path()
    target = Path(path).expanduser().resolve()
    repos, _ = read(p)
    if target in repos:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{target}\n")
    return True


def walk_repos(root: Path, depth: int = REPO_DEPTH) -> list[Path]:
    """Every git work tree at most `depth` below the root, nested ones included: a repository
    inside another's tree (servers/media_server) is not tracked by its parent, so the walk goes on
    below a repository rather than stopping at it."""
    found, frontier = [], [(Path(root), 0)]
    while frontier:
        d, depth_here = frontier.pop()
        if (d / ".git").exists():
            found.append(d)
        if depth_here == depth:
            continue
        try:
            kids = [k for k in d.iterdir() if k.is_dir() and not k.is_symlink()]
        except OSError:
            continue
        frontier += [
            (k, depth_here + 1)
            for k in kids
            if k.name not in SKIP_DIRS and not k.name.startswith(".")
        ]
    return sorted(found)


def existing(registry: Path | None = None) -> list[Path]:
    """The registered repos that are on disk and are git repositories."""
    repos, _ = read(registry)
    return [r for r in repos if r.exists() and (r / ".git").exists()]


def union(walked: Iterable[Path], registry: Path | None = None) -> tuple[list[Path], list[str]]:
    """(every repo in the estate, the registered paths that could not be used).

    The registry is UNIONED with the walk: a repo outside the walked root is in the estate
    because `install.sh` put it there, and a walked repo that was never registered is in it
    because it is on disk under the root.

    A registered path that is GONE is named, not skipped -- a moved repo leaves its old path
    behind, and the owner is told which path went away rather than guessing which repository
    stopped being audited.
    """
    found = {Path(r).resolve() for r in walked}
    problems: list[str] = []
    repos, bad = read(registry)
    for line in bad:
        problems.append(f"registry line is not an absolute path: {line}")
    for r in repos:
        if not r.exists():
            problems.append(f"registered but gone: {r}")
        elif not (r / ".git").exists():
            problems.append(f"registered but not a git repository: {r}")
        else:
            found.add(r)
    return sorted(found), problems
