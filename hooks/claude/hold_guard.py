#!/usr/bin/env python3
"""A Claude Code PreToolUse hook: nothing inside a HELD repo changes, and the refusal says why.

THE HOLD (2026-10-26, `lib/repo_hold.py`). ztomer/apes was archived on GitHub and its local clone
kept, marked `goh.archived` and made read-only (`chmod -R a-w`). A write then failed with a bare
"Permission denied" that named neither the reason nor the way out -- and an agent's reflex on
EACCES is `chmod u+w`, which undoes the layer that refused it. So this guard refuses, at the tool
boundary and with the hold's own reason and lift command:

  * Edit / Write / MultiEdit / NotebookEdit of a path inside a held repo;
  * a Bash command that would change one: a git verb that writes (commit, push, add, checkout,
    reset, stash, ...) run in it (`cd`, `-C`, or the session's cwd), and a file verb (rm, mv, cp
    INTO, touch, sed -i, tee, a redirection, chmod/chown/chflags -- the last so an agent cannot
    undo the read-only layer) naming a path inside one. A RECURSIVE verb on an ancestor
    (`chmod -R u+w ~/Projects`) is refused when a held repo lies below it.

Read-only commands (ls, cat, rg, git log/status/diff/show) run. A command it cannot split runs,
unjudged: the guard must never be what breaks an ordinary command. The read-only layer still
stands behind it. The way out is the hold's lift command (`scripts/release_hold.sh`, or
`scripts/unarchive_repo.sh` for an archive), which is not a write verb and so is never refused.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
try:
    import repo_hold
except ImportError:  # the shared reader is not there: fail OPEN, and say so
    repo_hold = None
    print("· hold guard: lib/repo_hold.py is not there -- holds are not enforced", file=sys.stderr)

# git verbs that change the repository (or its worktree) whatever their arguments.
GIT_WRITES = {
    "add", "am", "apply", "checkout", "cherry-pick", "clean", "commit", "fetch", "filter-branch",
    "gc", "init", "merge", "mv", "notes", "prune", "pull", "push", "rebase", "replace", "reset",
    "restore", "revert", "rm", "stash", "submodule", "switch", "update-index", "update-ref",
    "worktree", "lfs", "sparse-checkout", "maintenance", "repack",
}  # fmt: skip
# git verbs that write only with an argument or a writing flag (`git branch` lists; `-D x` deletes).
GIT_WRITES_WITH_ARGS = {"branch", "tag", "remote", "config", "symbolic-ref", "reflog"}
GIT_READ_FLAGS = {"--list", "-l", "--get", "--get-all", "--get-regexp", "--show-current",
                  "--contains", "--merged", "--no-merged", "get", "list"}  # fmt: skip

# File verbs: any path operand is written.
FILE_WRITES = {
    "rm", "rmdir", "unlink", "touch", "mkdir", "truncate", "shred", "tee", "chmod", "chown",
    "chgrp", "chflags", "xattr", "setfacl", "sd", "trash", "srm", "mv",
}  # fmt: skip
# Copy verbs: only the LAST operand (the destination) is written.
COPY_WRITES = {"cp", "ln", "install", "rsync", "ditto", "scp"}
IN_PLACE = {"sed", "gsed", "perl", "ruby"}  # write only with -i
RECURSIVE_FLAGS = {"-R", "-r", "--recursive", "-rf", "-fr", "-Rf", "-fR"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "1>", "2>", "1>>", "2>>"}
SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;"}


HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)(\w+)\1")


def _code(command: str) -> str:
    """The command with heredoc BODIES removed (text, not code) and each line its own command."""
    command = command.replace("\\\n", " ")
    out, lines, i = [], command.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for m in HEREDOC.finditer(line):
            while i < len(lines) and lines[i].strip() != m.group(2):
                i += 1
            i += 1
    return " ;\n".join(out)


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _segments(command: str) -> list[list[str]]:
    """Simple commands, with redirections kept as their own tokens."""
    segs: list[list[str]] = [[]]
    for tok in _tokens(_code(command)):
        if tok in SEPARATORS or (set(tok) <= set(";&|") and tok):
            segs.append([])
        else:
            segs[-1].append(tok)
    return [s for s in segs if s]


def _strip_prefix(seg: list[str]) -> list[str]:
    """Drop env assignments and wrappers (`FOO=1 sudo -n env nice rm x` -> `rm x`)."""
    wrappers = {"sudo", "env", "nice", "nohup", "time", "command", "builtin", "exec", "xargs"}
    i = 0
    while i < len(seg):
        tok = seg[i]
        if "=" in tok and not tok.startswith(("-", "=")) and tok.split("=")[0].isidentifier():
            i += 1
        elif tok in wrappers:
            i += 1
            while i < len(seg) and seg[i].startswith("-"):
                i += 1
        else:
            break
    return seg[i:]


def _operands(args: list[str]) -> list[str]:
    out, after_dashdash = [], False
    for a in args:
        if a == "--" and not after_dashdash:
            after_dashdash = True
        elif after_dashdash or not a.startswith("-"):
            out.append(a)
    return out


def _held(path: str, cwd: str, recursive: bool) -> list:
    hold = repo_hold.find_hold(path, cwd)
    if hold:
        return [hold]
    return repo_hold.holds_below(path, cwd) if recursive else []


def _git_target(args: list[str], here: str) -> tuple[str, list[str]]:
    """git's own options consumed: (the directory it runs in, the verb and its arguments)."""
    while args and args[0].startswith("-"):
        if args[0] == "-C" and len(args) > 1:
            here = os.path.join(here, os.path.expanduser(args[1]))
            args = args[2:]
        elif args[0] in ("-c", "--git-dir", "--work-tree", "--namespace") and len(args) > 1:
            if args[0] in ("--git-dir", "--work-tree"):
                here = os.path.join(here, os.path.expanduser(args[1]))
            args = args[2:]
        else:
            args = args[1:]
    return here, args


def _git_writes(verb: str, rest: list[str]) -> bool:
    if verb in GIT_WRITES:
        return not (verb == "stash" and rest[:1] in (["list"], ["show"]))
    if verb in GIT_WRITES_WITH_ARGS:
        if any(a in GIT_READ_FLAGS for a in rest):
            return False
        if verb == "config":
            return len(_operands(rest)) >= 2 or any(a.startswith("--unset") for a in rest)
        if verb == "remote":
            return bool(rest) and rest[0] not in ("-v", "show", "get-url")
        return bool(_operands(rest)) or any(a in ("-d", "-D", "-m", "-M") for a in rest)
    return False


def judge_command(command: str, cwd: str) -> str | None:
    """The refusal for a Bash command that would change a held repo, or None."""
    if repo_hold is None:
        return None
    try:
        segments = _segments(command)
    except ValueError:
        return None
    here = cwd
    for raw in segments:
        # Redirections first: `> path` writes path, whatever the command.
        seg: list[str] = []
        i = 0
        while i < len(raw):
            tok = raw[i]
            if tok in REDIRECTS or (tok.endswith(">") and tok.rstrip(">").isdigit()):
                if i + 1 < len(raw) and raw[i + 1] not in ("&",) and raw[i + 1] != "/dev/null":
                    for hold in _held(raw[i + 1], here, False):
                        return hold.describe(f"writing {raw[i + 1]} (a redirection)")
                i += 2
                continue
            if tok in ("<", "<<", "<<<", ">&", "<&"):
                i += 2
                continue
            seg.append(tok)
            i += 1
        seg = _strip_prefix(seg)
        if not seg:
            continue
        verb, args = os.path.basename(seg[0]), seg[1:]
        lifters = [i for i, tok in enumerate(seg) if os.path.basename(tok) == "release_hold.sh"]
        for target in _operands(seg[lifters[0] + 1 :]) if lifters else []:
            for hold in _held(target, here, False):
                return (
                    f"{hold.describe('`release_hold.sh`')} -- but a hold is lifted by the OWNER, "
                    "from their own terminal, never by an agent: ask them to run it."
                )
        if verb in ("cd", "pushd") and len(args) == 1:
            here = os.path.join(here, os.path.expanduser(args[0]))
            continue
        if verb == "git":
            where, rest = _git_target(args, here)
            if rest and _git_writes(rest[0], rest[1:]):
                for hold in _held(where, here, False):
                    return hold.describe(f"`git {' '.join(rest[:3])}`")
            continue
        recursive = any(a in RECURSIVE_FLAGS for a in args)
        targets: list[str] = []
        if verb in FILE_WRITES:
            ops = _operands(args)
            if verb in ("chmod", "chown", "chgrp", "chflags") and ops:
                ops = ops[1:]  # the mode / owner / flags operand is not a path
            targets = ops
        elif verb in COPY_WRITES:
            ops = _operands(args)
            targets = ops[-1:] if len(ops) >= 2 else []
        elif verb in IN_PLACE and any(a.startswith(("-i", "-pi")) for a in args):
            targets = _operands(args)[1:]
        elif verb == "find" and any(a in ("-delete", "-exec", "-execdir") for a in args):
            targets, recursive = [a for a in args if not a.startswith("-")][:1], True
        elif verb == "dd":
            targets = [a[3:] for a in args if a.startswith("of=")]
        for target in targets:
            for hold in _held(target, here, recursive):
                return hold.describe(f"`{verb}` on {target}")
    return None


def judge_path(path: str, tool: str, cwd: str) -> str | None:
    if repo_hold is None or not path:
        return None
    hold = repo_hold.find_hold(path, cwd)
    return hold.describe(f"{tool} of {path}") if hold else None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        reason = judge_path(path, tool, cwd)
    elif tool == "Bash":
        reason = judge_command(tool_input.get("command") or "", cwd)
    else:
        reason = None
    if reason:
        json.dump(
            {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": reason}},
            sys.stdout,
        )  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
