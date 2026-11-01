"""Every Cargo.lock a repo tracks, and the `.cargo/audit.toml` each one is judged by (GOH.EA2).

`rust_gate.sh` audited only the lock beside the manifest it was handed, so a repo of several cargo
workspaces -- monitor's `fuzz/`, media_server's 31 crates and testkits, koffee_big's core beside an
app -- hand-wrote one audit step per extra lock, each naming its target through a shell variable
the estate audit cannot read (`cd "$rust_core" && cargo audit`, `cargo audit -f "$crate/..."`).
So the gate audits EVERY tracked lock of the repo, and this module is the one rule for which:

* THE LOCKS: `git ls-files` of `Cargo.lock` and `*/Cargo.lock` under the repo directory, plus the
  lock of the workspace the gate was pointed at (it is certified whether or not it is tracked yet).
* THE CONFIG: cargo-audit reads `.cargo/audit.toml` from its CURRENT directory and nowhere else, so
  where it runs decides which ignore list applies. A lock is audited from the nearest directory at
  or above it, inside the repo, that holds a `.cargo/audit.toml` -- cargo's own config lookup --
  and from its own directory when none does. media_server keeps one policy at its root for 31
  locks; monitor's `fuzz/` keeps its own beside its lock; both are read as written.

The gate runs `plan` (one line per lock: `<config dir>\\t<lock>`, repo-relative, sorted);
`checks/check_audit_ignores.py --repo` asks `governed` which locks its config judges, so a
shared ignore entry is stale only when it fires against none of them; and `lib/gate_coverage.py`
models `rust_gate.sh` with `plan` too. One rule, three readers, no copy.

    python3 lib/rust_locks.py plan <repo> [<extra lock>]
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

import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

CONFIG = PurePosixPath(".cargo") / "audit.toml"


def tracked_locks(repo: Path) -> list[str]:
    """Repo-relative tracked lockfiles under `repo` (empty outside a git checkout)."""
    r = subprocess.run(
        ["git", "ls-files", "-z", "--", "Cargo.lock", "*/Cargo.lock"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return sorted(p for p in r.stdout.split("\0") if p) if r.returncode == 0 else []


def config_dir(lock: str, has_config: Callable[[str], bool]) -> str:
    """The directory cargo-audit runs in for `lock`: the nearest at or above it with a
    `.cargo/audit.toml`, else the lock's own. `has_config(dir)` answers for a repo-relative dir."""
    own = str(PurePosixPath(lock).parent)
    d = own
    while True:
        if has_config(d):
            return d
        if d == ".":
            return own
        d = str(PurePosixPath(d).parent)


def plan(locks: Iterable[str], has_config: Callable[[str], bool]) -> list[tuple[str, str]]:
    """(config dir, lock) for every lock, sorted by lock, each once."""
    return [(config_dir(lk, has_config), lk) for lk in sorted(set(locks))]


def on_disk(repo: Path) -> Callable[[str], bool]:
    return lambda d: (repo / d / CONFIG).is_file()


def repo_plan(repo: Path, extra: str | None = None) -> list[tuple[str, str]]:
    """The plan for a checkout: its tracked locks, plus `extra` (a path) when it lies inside."""
    repo = repo.resolve()
    locks = tracked_locks(repo)
    if extra:
        try:
            locks.append(Path(extra).resolve().relative_to(repo).as_posix())
        except ValueError:
            pass  # the workspace climbed out of the repo: not this repo's lock to plan
    return plan(locks, on_disk(repo))


def governed(repo: Path, cfg: str, extra: str | None = None) -> list[str]:
    """The locks whose audit runs from `cfg` (repo-relative): the ones its ignore list judges."""
    return [lk for c, lk in repo_plan(repo, extra) if c == cfg]


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3) or argv[0] != "plan":
        print("usage: rust_locks.py plan <repo> [<extra lock>]", file=sys.stderr)
        return 2
    rows = repo_plan(Path(argv[1]), argv[2] if len(argv) == 3 else None)
    if not rows:
        print(f"rust_locks: no Cargo.lock in {os.path.abspath(argv[1])}", file=sys.stderr)
        return 1
    for cfg, lock in rows:
        print(f"{cfg}\t{lock}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
