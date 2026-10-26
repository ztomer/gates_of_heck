#!/usr/bin/env python3
"""THE HOLD: a repo that must not be changed, and says why and how to lift it (2026-10-26).

A hold is one key in the repo's LOCAL git config, `goh.hold`, whose value is the reason and the way
out (`scripts/hold_repo.sh` writes `<date>: <reason> -- lift: <command>`). Archiving is one use of
it (`scripts/archive_repo.sh`); an upstream clone that belongs to someone else, a repo frozen during
a release, and a repo an owner ruling parks are others. `goh.archived`, the key the first archive
(ztomer/apes, 2026-10-26) was marked with by hand, is read as a hold too, lifted by
`scripts/unarchive_repo.sh`.

WHO READS IT. The git gates (commit, commit-msg -- so a merge --, pre-applypatch, push) refuse a
held repo through `check`, and the Claude Code guard (`hooks/claude/hold_guard.py`) refuses an
edit or a changing command inside one. Both name the hold's value, so the refusal says WHY, which
the read-only layer (`chmod -R a-w`) alone cannot: a bare EACCES names nothing.

WHY NO GIT. The guard runs on every tool call, so the config is PARSED here, not asked of `git
config`: walk up from the path to each `.git` (a directory, or a worktree's/submodule's `gitdir:`
file whose `commondir` names the shared directory), read `config`, take the `[goh]` section. Every
enclosing repo is read, not just the nearest: a submodule inside a held repo is inside the hold.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

KEYS = ("hold", "archived")  # [goh] keys that are holds; `archived` is the first one's spelling
SKIP_DIRS = {"node_modules", "target", ".build", "build", "dist", "__pycache__", ".venv", "venv"}


@dataclass(frozen=True)
class Hold:
    root: str  # the held repo's worktree top
    key: str  # goh.hold or goh.archived
    value: str  # the reason, and (for goh.hold) the way out

    def lift(self) -> str:
        """The command that lifts this hold."""
        goh = os.environ.get("GOH_DIR") or os.path.expanduser("~/Projects/gates_of_heck")
        if self.key == "archived":
            return f'"{goh}/scripts/unarchive_repo.sh" {self.root}'
        marker = " -- lift: "
        if marker in self.value:
            return self.value.rsplit(marker, 1)[1].strip()
        return f'"{goh}/scripts/release_hold.sh" {self.root}'

    def reason(self) -> str:
        return self.value.rsplit(" -- lift: ", 1)[0].strip()

    def describe(self, action: str) -> str:
        """The refusal, one paragraph: what was refused, where, why, and the way out."""
        return (
            f"{self.root} is on hold (goh.{self.key}): {self.reason()}. "
            f"Nothing in it may change, so {action} is refused. "
            f"To lift the hold: {self.lift()}"
        )


def _unquote(raw: str) -> str:
    """A git config value: quotes removed, escapes resolved, an unquoted comment dropped."""
    out, quoted, i = [], False, 0
    raw = raw.strip()
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(raw[i + 1], raw[i + 1]))
            i += 2
            continue
        if c == '"':
            quoted = not quoted
        elif c in "#;" and not quoted:
            break
        else:
            out.append(c)
        i += 1
    return "".join(out).strip()


def config_holds(config_path: str) -> list[tuple[str, str]]:
    """The (key, value) holds in one git config file, in KEYS order; [] when unreadable."""
    try:
        with open(config_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    found: dict[str, str] = {}
    section = ""
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            section = s[1 : s.find("]")].strip().lower() if "]" in s else ""
            continue
        if section != "goh" or "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.strip().lower()
        if key in KEYS:
            found[key] = _unquote(value)  # the last one wins, as git's --get does
    return [(k, found[k]) for k in KEYS if found.get(k)]


def _common_dir(dot_git: str) -> str | None:
    """The directory holding the repo's shared config, for a `.git` dir or `gitdir:` file."""
    if os.path.isdir(dot_git):
        return dot_git
    try:
        with open(dot_git, encoding="utf-8") as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    if not first.startswith("gitdir:"):
        return None
    gitdir = first[len("gitdir:") :].strip()
    gitdir = os.path.normpath(os.path.join(os.path.dirname(dot_git), gitdir))
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as fh:
            common = fh.readline().strip()
    except OSError:
        return gitdir  # a submodule's gitdir is its own common dir
    return os.path.normpath(os.path.join(gitdir, common))


def repo_hold(root: str) -> Hold | None:
    """The hold on the repo whose worktree top is `root`, or None."""
    common = _common_dir(os.path.join(root, ".git"))
    if common is None:
        return None
    holds = config_holds(os.path.join(common, "config"))
    return Hold(root, *holds[0]) if holds else None


def _absolute(path: str, cwd: str | None = None) -> str:
    path = os.path.expanduser(path)
    if not os.path.isabs(path):
        path = os.path.join(cwd or os.getcwd(), path)
    return os.path.normpath(path)


def find_hold(path: str, cwd: str | None = None) -> Hold | None:
    """The hold on any repo enclosing `path` (which need not exist), or None."""
    here = _absolute(path, cwd)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            hold = repo_hold(here)
            if hold:
                return hold
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def holds_below(
    path: str, cwd: str | None = None, depth: int = 3, budget: int = 4000
) -> list[Hold]:
    """Held repos at or BELOW `path`, for a recursive verb on an ancestor (`chmod -R u+w ~/Projects`).

    Bounded: `depth` directory levels and `budget` directories, skipping dot and build directories,
    so a recursive command on `~` costs milliseconds, not a disk walk. A held repo deeper than that
    is not seen -- its read-only layer still stands.
    """
    top = _absolute(path, cwd)
    if not os.path.isdir(top):
        return []
    found: list[Hold] = []
    frontier, seen = [(top, 0)], 0
    while frontier and seen < budget:
        here, level = frontier.pop()
        seen += 1
        if os.path.exists(os.path.join(here, ".git")):
            hold = repo_hold(here)
            if hold:
                found.append(hold)
                continue
        if level >= depth:
            continue
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP_DIRS:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    frontier.append((e.path, level + 1))
            except OSError:
                continue
    return found


def main(argv: list[str]) -> int:
    """`repo_hold.py check <path> [--action <what>]`: exit 1 with the refusal when held, else 0.
    `repo_hold.py show <path>`: print the hold's key and value (exit 1 when there is none)."""
    if len(argv) < 2 or argv[0] not in ("check", "show"):
        print("usage: repo_hold.py check|show <path> [--action <what>]", file=sys.stderr)
        return 2
    action = "this change"
    if "--action" in argv:
        i = argv.index("--action")
        action = argv[i + 1] if i + 1 < len(argv) else action
    hold = find_hold(argv[1])
    if argv[0] == "show":
        if hold is None:
            return 1
        print(f"goh.{hold.key}\t{hold.value}")
        return 0
    if hold is None:
        return 0
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from tui.lib import err
    except ImportError:

        def err(message: str) -> None:
            print(f"✗ {message}", file=sys.stderr)

    err(f"{action}: {hold.root} is on hold (goh.{hold.key})")
    err(f"  why: {hold.reason()}")
    err(f"  lift it: {hold.lift()}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
