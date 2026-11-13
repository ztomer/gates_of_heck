#!/usr/bin/env python3
"""hooks_refresh.py -- `goh hooks`: every estate repo whose stock hooks are stale, and one command
that refreshes them all (GOH.HOOKREFRESH).

Stock hooks are COPIED (install.sh), and structural.sh refuses a commit run by an older stock
copy -- rightly: that repo is not running today's gate. But nothing refreshed them when goh moved,
so on 2026-11-12/13 four repos were refused in turn and an agent ran install.sh by hand in each.

WHY A COMMAND AND NOT AN AUTOMATIC REFRESH. The hooks are TRACKED files of the consumer. A gate
that rewrote them would dirty the tree it judges (structural.sh says why it never does); a goh
release that rewrote them would write into a dozen repos other agents are working in, at a moment
none of them chose. A listing that the daily estate audit records (and the session hook reads),
plus one command run deliberately, refreshes the same hooks with nobody's tree moved under them.

    goh hooks                       # list: one line per repo with a stale hook; exit 1 if any
    goh hooks --refresh             # install.sh each, then commit `.githooks` alone
    goh hooks --refresh --except ~/.claude/skills   # named, listed, never touched

The refresh is install.sh's own (GOH_SKIP_BUILD=1): it overwrites a hook only when it is pristine
-- today's stock, a stock goh once shipped, or the one it recorded installing -- so a hook a repo
edited is never clobbered. A repo is set aside, named, when it is on hold, when its `.githooks`
has uncommitted changes (the commit would sweep them in), or when it lacks the tools/gate.sh and
.gatesrc install.sh would otherwise write as starters. The commit carries `.githooks` only,
through the repo's own hooks (the refreshed ones); one they refuse is undone, so the repo is left
exactly as it was.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "checks"))
from _gitutil import foreign_repo_env

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tui.lib import counted, err, info, ok, warn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import estate_registry
import hook_currency
import repo_hold

GOH = Path(__file__).resolve().parent.parent
MESSAGE = "chore: refresh goh's stock hooks ({hooks}) -- goh hooks --refresh"


def estate(root: Path) -> tuple[list[Path], list[str]]:
    """The estate the audit reads: the walk under `root` unioned with install.sh's registry."""
    walked = estate_registry.walk_repos(root, estate_registry.REPO_DEPTH)
    return estate_registry.union(
        walked, estate_registry.registry_path(estate_registry.state_file())
    )


def _where(repo: Path, root: Path) -> str:
    try:
        return repo.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(repo)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
        stdin=subprocess.DEVNULL,
    )


def _tail(p: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in (p.stdout + p.stderr).splitlines() if ln.strip()]
    return " | ".join(lines[-3:])


def refresh(repo: Path, hooks: list[str]) -> tuple[bool, str]:
    """(refreshed, one line saying what happened)."""
    if (hold := repo_hold.repo_hold(str(repo))) is not None:
        return False, f"set aside: on hold (goh.{hold.key}), nothing in it may change"
    if not ((repo / "tools" / "gate.sh").is_file() and (repo / ".gatesrc").is_file()):
        return False, "set aside: no tools/gate.sh or .gatesrc (install.sh would write starters)"
    dirty = _git(repo, "status", "--porcelain", "--", ".githooks")
    if dirty.returncode != 0 or dirty.stdout.strip():
        return False, "set aside: .githooks has uncommitted changes (commit or drop them first)"
    env = foreign_repo_env()
    env["GOH_SKIP_BUILD"] = "1"
    env["GOH_DIR"] = str(GOH)
    # install.sh exits non-zero when it refused an EDITED hook; that is expected here, so the
    # verdict is the re-read: no stale hook left.
    inst = subprocess.run(
        ["bash", str(GOH / "install.sh"), str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    left = hook_currency.stale(repo, GOH, foreign_repo_env())
    if left:
        return False, f"install.sh left {', '.join(left)} stale: {_tail(inst)}"
    _git(repo, "add", "--", ".githooks")
    if not _git(repo, "diff", "--cached", "--name-only", "--", ".githooks").stdout.strip():
        return True, "refreshed (nothing to commit)"
    done = _git(
        repo, "commit", "-q", "-m", MESSAGE.format(hooks=", ".join(hooks)), "--", ".githooks"
    )
    if done.returncode != 0:
        # Put `.githooks` back as it was (it was clean above, so this drops only what install.sh
        # wrote): a refusal must not leave another agent's tree dirty.
        _git(repo, "restore", "--source=HEAD", "--staged", "--worktree", "--", ".githooks")
        _git(repo, "clean", "-fdq", "--", ".githooks")
        return False, f"NOT refreshed: its commit gate refused ({_tail(done)}); left as it was"
    sha = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    return True, f"refreshed {', '.join(hooks)} in {sha}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("GOH_ESTATE_ROOT", Path.home() / "Projects")),
    )
    ap.add_argument("--refresh", action="store_true", help="install.sh and commit each stale repo")
    ap.add_argument(
        "--except",
        dest="kept",
        action="append",
        type=Path,
        default=[],
        help="a repo to list but never refresh (repeatable)",
    )
    a = ap.parse_args(argv)
    root = a.root.resolve()
    kept = {p.expanduser().resolve() for p in a.kept}
    repos, _ = estate(root)
    env = foreign_repo_env()
    found = [(r, s) for r in repos if (s := hook_currency.stale(r, GOH, env))]
    if not found:
        ok(f"no stale stock hook in {counted(len(repos), 'estate repo')}")
        return 0
    failed = 0
    for repo, hooks in found:
        where = _where(repo, root)
        if not a.refresh:
            warn(f"{where}: {', '.join(hooks)} stale")
        elif repo.resolve() in kept:
            info(f"{where}: {', '.join(hooks)} stale, set aside (--except): left for its owner")
        else:
            done, said = refresh(repo, hooks)
            (ok if done else err)(f"{where}: {said}")
            failed += not done
    if not a.refresh:
        info("refresh them all: goh hooks --refresh [--except <repo>]")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
