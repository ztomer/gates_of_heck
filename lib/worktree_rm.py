"""A recursive rm of a path holding a git work tree with uncommitted tracked changes is refused
before it runs (LOOP.MISTAKEGUARDS, mistake 1).

THE CASE (2026-11-22). The coordinator ran `rm -rf` on a scratch directory to tidy up. It held two
registered git worktrees whose tracked files carried uncommitted edits; the delete took the edits
with it, and no reflog keeps a working-tree change. `git worktree remove` refuses exactly this
(it needs `--force` for a dirty tree); a bare `rm -rf` asks nothing.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`: quoted text, heredoc bodies and
comments are not commands): `rm` with `-r`/`-R`/`--recursive`, each operand expanded the way the
shell would (`~`, `$VAR`, a glob; relative to the `cd` before it, else to the session's cwd), and
walked for `.git` entries -- a linked worktree's `.git` file or a checkout's `.git` directory. One
whose `git status` shows modified, staged or deleted TRACKED files is named with its files, and
the command is refused. Untracked files are not the rule's: `git worktree remove` drops them too.
The walk is bounded (WALK_LIMIT directories): a tree too large to read in a hook runs, unjudged, as
every unparseable command does. The escape is `# rm-ok: <reason>`.

The discard guard's refusal of a recursive `rm` whose operand (expanded as the shell would,
relative to the `cd` before it or the session cwd) holds a git work tree -- a linked worktree's
`.git` file or a checkout's `.git` directory -- with uncommitted TRACKED changes, naming the tree
and its files: the coordinator's `rm -rf` of a scratch dir took two worktrees' edits with it
(LOOP.MISTAKEGUARDS, 2026-11-22). A walk past 20000 directories runs unjudged. Escape:
"""

from __future__ import annotations

import glob
import os
import re

import gitrun
import shell_segments

ESCAPE = re.compile(r"#\s*rm-ok:\s*\S")
WALK_LIMIT = 20000  # directories read per command before the rule gives up (fails open)
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".ruff_cache"}
WAY_OUT = (
    "No reflog keeps a working-tree change, and `git worktree remove` would have refused this "
    "without `--force`. Commit (or `goh undo`-able WIP) first, then `git worktree remove <wt>`; or "
    "delete a narrower path. To delete it anyway, add `# rm-ok: <reason>` to the command."
)


def _recursive(args: list[str]) -> bool:
    for a in args:
        if a == "--":
            return False
        if a == "--recursive" or (
            a.startswith("-") and not a.startswith("--") and re.search("[rR]", a[1:])
        ):
            return True
    return False


def _operands(args: list[str]) -> list[str]:
    if "--" in args:
        i = args.index("--")
        return [a for a in args[:i] if not a.startswith("-")] + args[i + 1 :]
    return [a for a in args if not a.startswith("-")]


def _expand(operand: str, here: str) -> list[str]:
    path = os.path.expandvars(os.path.expanduser(operand))
    if "$" in path:  # an unset variable: the shell's value is unknown, so is the path
        return []
    path = os.path.join(here, path)
    if glob.has_magic(path):
        return glob.glob(path)
    return [path]


def _trees(root: str) -> list[str] | None:
    """The work trees at or under `root` (a `.git` file or directory), or None past the bound."""
    if not os.path.isdir(root) or os.path.islink(root):
        return []
    found, stack, seen = [], [root], 0
    while stack:
        here = stack.pop()
        seen += 1
        if seen > WALK_LIMIT:
            return None
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for e in entries:
            if e.name == ".git":
                found.append(here)
            elif e.name not in SKIP and e.is_dir(follow_symlinks=False):
                stack.append(e.path)
    return sorted(found)


def dirty(tree: str) -> list[str]:
    """`tree`'s tracked files with uncommitted changes (modified, staged, deleted), or []."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = gitrun.run_reader(
            ["status", "--porcelain", "--untracked-files=no"], repo=tree, timeout=10, env=env
        )
    except Exception:  # noqa: BLE001 -- a git that cannot answer must not wall the command
        return []
    if r.returncode != 0:
        return []
    return [ln[3:] for ln in r.stdout.splitlines() if len(ln) > 3]


def judge(command: str, cwd: str) -> str | None:
    """The refusal for a recursive rm whose path holds a dirty git work tree, or None."""
    if ESCAPE.search(command):
        return None
    try:
        segments = shell_segments.segments(command)
    except ValueError:
        return None
    here = cwd
    for seg in segments:
        prog, args = shell_segments.program(seg)
        if prog == "cd" and len(args) == 1:
            here = os.path.join(here, os.path.expanduser(args[0]))
            continue
        if prog != "rm" or not _recursive(args):
            continue
        for operand in _operands(args):
            for path in _expand(operand, here):
                for tree in _trees(path) or []:
                    files = dirty(tree)
                    if files:
                        shown = ", ".join(files[:8]) + (
                            f" (+{len(files) - 8} more)" if len(files) > 8 else ""
                        )
                        return (
                            f"`{' '.join(seg)}` would delete the git work tree {tree}, whose "
                            f"tracked files have uncommitted changes: {shown}. {WAY_OUT}"
                        )
    return None


__all__ = ["ESCAPE", "dirty", "judge"]
