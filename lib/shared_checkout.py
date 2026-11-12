"""A stash, reset or branch switch in a SHARED checkout is refused before it runs (GOH.NORESET).

THE CASE. "Never stash, reset or switch branches in the shared checkout" was a brief line. On
2026-11-09 an RS.TP agent ran a bare `git reset -q` in token-pool after a refused commit, and on
2026-11-11 another ran one more: each unstaged its own files that time, but the same command
unstages every other session's staged work in that checkout, a `git stash` takes their unstaged
edits away, and a `git checkout <branch>` moves the tree out from under every writer at once. An
earlier ZeroThunder brief had to allow `git restore --staged` by hand.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`: quoted text, heredoc bodies and
comments are not commands), for a git command whose repository is a PRIMARY checkout (its git dir
is the common git dir -- the checkout every session shares; a linked worktree is its creator's):
  * `git stash` in any form that moves work (`list` and `show` only read it);
  * `git reset` in any form (`git restore --staged <paths>` is the pathed unstage);
  * a branch switch: `git switch`, `git checkout <branch>`, `git checkout -b/-B/--orphan/--detach`,
    `git checkout -` (a `git checkout [<rev>] -- <paths>` restores files and is the discard
    guard's question, not this one);
  * `git restore --staged` (or `-S`) with no named path, or a pathspec naming the whole tree
    (`.`, `:/`, `*`).
Refused with the way forward: a worktree of your own (`git worktree add build/<name> -b <branch>`),
or `git restore --staged <your paths>`. The one escape is `# reset-ok: <reason>`.
"""

from __future__ import annotations

import os
import re
import subprocess

import shell_segments

try:
    import gitrun
except ImportError:  # no deadline helper: the rule answers "not a repository" and lets it run
    gitrun = None

ESCAPE = re.compile(r"#\s*reset-ok:\s*\S")
WHOLE_TREE = {".", ":/", ":/*", "*", "./", ":"}
_STASH_READS = {"list", "show"}
_GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

WAY_OUT = (
    "This checkout is shared: every session's staged and unstaged work lives in it, and this "
    "command moves theirs too. Work in a worktree of your own (`git worktree add build/<name> -b "
    "<branch>`, removed when done), unstage only your own files with `git restore --staged "
    "<paths>`, and keep a WIP as a commit on your branch, never a stash. To do it here on purpose, "
    "add `# reset-ok: <reason>` to the command."
)


def _git(where: str, *args: str) -> str | None:
    if gitrun is None:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        done = gitrun.run_reader(args, repo=where, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def is_shared(where: str) -> bool:
    """Is `where` inside a PRIMARY checkout (git dir == common git dir)? False outside a repo."""
    out = _git(where, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    if not out or len(out.splitlines()) != 2:
        return False
    git_dir, common = out.splitlines()
    return os.path.realpath(git_dir) == os.path.realpath(common)


def _is_rev(where: str, word: str) -> bool:
    return _git(where, "rev-parse", "--verify", "-q", f"{word}^{{commit}}") is not None


def _what(where: str, verb: str, rest: list[str]) -> str | None:
    """What this git verb does to a shared checkout, or None when it moves nobody's work."""
    flags = [a for a in rest if a.startswith("-")]
    words = [a for a in rest if not a.startswith("-")]
    if verb == "stash":
        if words and words[0] in _STASH_READS:
            return None
        return "a stash takes every session's uncommitted edits out of the tree"
    if verb == "reset":
        return "a reset moves the index (and with --hard or --keep the tree) for every session"
    if verb == "switch":
        return None if {"-h", "--help"} & set(flags) else "a branch switch moves the tree"
    if verb == "checkout":
        if "--" in rest or {"-p", "--patch", "-h", "--help"} & set(flags):
            return None  # restores files: the discard guard's question
        if {"-b", "-B", "--orphan", "--detach"} & set(flags) or rest[:1] == ["-"]:
            return "a branch switch moves the tree"
        if len(words) == 1 and _is_rev(where, words[0]):
            return "a branch switch moves the tree"
        return None
    if verb == "restore" and {"--staged", "-S"} & set(flags):
        paths = [w for w in words if w != "--"]
        if not paths or WHOLE_TREE & set(paths):
            return "an unstage of the whole index takes every session's staged work out"
    return None


def judge(command: str, cwd: str) -> str | None:
    """The refusal for a stash, reset or branch switch in a shared checkout, or None."""
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
        if prog != "git":
            continue
        where = here
        while args and args[0] in _GIT_VALUED and len(args) > 1:
            if args[0] == "-C":
                where = os.path.join(where, os.path.expanduser(args[1]))
            args = args[2:]
        if not args:
            continue
        why = _what(where, args[0], args[1:])
        if why and is_shared(where):
            return f"`{' '.join(seg)}` in {where}: {why}. {WAY_OUT}"
    return None


__all__ = ["ESCAPE", "is_shared", "judge"]
