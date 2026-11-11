#!/usr/bin/env python3
"""A Claude Code PreToolUse hook: nothing inside a HELD repo changes, and the refusal says why.

Two kinds of hold, one judge (the Bash parsing below serves both):

  * the REPO's own hold (`goh.hold` / `goh.archived` in its git config, `lib/repo_hold.py`): no
    one changes it, the main thread included;
  * the LOOP QUEUE's hold (LOOP.FOREIGNEDIT, `lib/queue_hold.py`): a repo an in-progress item holds
    is changed only by that item's agent. Judged only for a SUBAGENT (the event's `agent_id`, which
    `loopctl start ID AGENTID` records on the item); the main thread is the coordinator and is never
    refused. `git fetch` and `git worktree` (the sanctioned way into a repo another agent holds) are
    not writes to its checkout here.

A subagent's direct GitHub Actions poll (`gh run watch/view/list`, `gh api .../actions`, `gh pr
checks --watch`) is refused here too, naming loopctl's one CI door (LOOP.GHDOOR, `ci_door.py`).

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

THE TARGET IS THE OPERAND'S OWN PATH (GOH.GUARDSCRATCH, 2026-11-11). A write is judged by the
repo enclosing the path it names, resolved to an absolute path, and by nothing else: a sed or perl
SCRIPT is not a path (BSD sed's `-i ''` suffix no longer shifts it into the operands), an empty
operand is no path, and a `$VAR` the same command assigned (`S=dir; sed -i '' x $S/f`) is expanded
first; an operand whose head is still unexpanded names nothing knowable and is not resolved against
the cwd (only its literal prefix, when it has one, is judged). ZT.EMBEDSEAM's agent was refused a
`sed -i` on its own scratch file because the script and `$S/m.sh` were each resolved against a cwd
in a repo another item held.

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
    import queue_hold  # the loop queue's holds (LOOP.FOREIGNEDIT)
except ImportError:
    queue_hold = None
try:
    import repo_hold
except ImportError:  # the shared reader is not there: fail OPEN, and say so
    repo_hold = None
    print("· hold guard: lib/repo_hold.py is not there -- holds are not enforced", file=sys.stderr)
try:  # the tools whose input holds a shell command: ONE list, `lib/shell_tools.py`
    from shell_tools import COMMAND_FIELD, SHELL_TOOLS
except ImportError:
    SHELL_TOOLS, COMMAND_FIELD = ("Bash",), "command"

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
# git verbs that touch only the shared object store or add a worktree: another agent's way in, never
# a write to the holder's checkout (a queue hold only; a repo hold refuses them as before).
GIT_BESIDE = {"fetch", "worktree"}
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
    """Drop env assignments, wrappers and the shell's own keywords (`FOO=1 sudo -n env nice rm x`
    -> `rm x`; `do rm x`, `then git commit` -> the command they lead)."""
    wrappers = {"sudo", "env", "nice", "nohup", "time", "command", "builtin", "exec", "xargs"}
    keywords = {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}
    i = 0
    while i < len(seg):
        tok = seg[i]
        if tok in keywords or (
            "=" in tok and not tok.startswith(("-", "=")) and tok.split("=")[0].isidentifier()
        ):
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


def _held(path: str, cwd: str, recursive: bool, agent: str | None = None) -> list:
    """The holds a write to `path` meets: the repo's own, then (for a subagent) the queue's. A
    relative path after a `cd` the guard could not follow (`cwd` "") names nothing knowable."""
    if not cwd and not os.path.isabs(os.path.expanduser(path)):
        return []
    hold = repo_hold.find_hold(path, cwd)
    if hold:
        return [hold]
    found = repo_hold.holds_below(path, cwd) if recursive else []
    if found or not agent or queue_hold is None:
        return found
    qhold = queue_hold.find(path, cwd, agent)
    if qhold:
        return [qhold]
    return queue_hold.holds_below(path, cwd, agent) if recursive else []


VAR = re.compile(r"\$(?:\{(\w+)\}|(\w+))")


def _assigns(seg: list[str]) -> dict[str, str] | None:
    """A segment that only assigns (`S=dir`, `export S=dir`): its NAME -> value, else None."""
    toks = seg[1:] if seg[:1] == ["export"] else seg
    pairs = [t.split("=", 1) for t in toks]
    if toks and all(len(p) == 2 and p[0].isidentifier() for p in pairs):
        return dict(pairs)
    return None


def _expand(tok: str, shell: dict[str, str], whole: bool = False) -> str | None:
    """TOK with the command's own assignments (and $HOME) expanded; a part still unexpandable cuts
    it to its literal prefix (None with `whole`); None when nothing literal is left."""
    known = {"HOME": os.path.expanduser("~"), **shell}
    tok = VAR.sub(lambda m: known.get(m.group(1) or m.group(2), m.group(0)), tok)
    cut = min((i for i in (tok.find("$"), tok.find("`")) if i >= 0), default=-1)
    if cut >= 0:
        if whole:
            return None
        tok = tok[:cut]
    return tok or None


# Options of an in-place editor that take the next token as their argument (never a path written).
SED_ARG_OPTS = {"-e", "-f", "--expression", "--file", "-l"}
PERL_ARG_OPTS = {"-e", "-E", "-I", "-M", "-m"}


def _in_place_targets(verb: str, args: list[str]) -> list[str]:
    """The files `sed -i` / `perl -pi` (gsed, ruby) rewrite: its operands less the script, which is
    the first operand only when no -e/-f gave it. BSD sed's `-i SUFFIX` takes the next token when
    it is empty or a `.ext`; an empty token is never a path."""
    if not any(a.startswith(("-i", "-pi", "--in-place")) for a in args):
        return []
    sed = verb in ("sed", "gsed")
    takes = SED_ARG_OPTS if sed else PERL_ARG_OPTS
    ops: list[str] = []
    script_given, i = False, 0
    while i < len(args):
        a, nxt = args[i], (args[i + 1] if i + 1 < len(args) else None)
        if a == "--":
            ops += args[i + 1 :]
            break
        if sed and a == "-i" and nxt is not None and (nxt == "" or nxt.startswith(".")):
            i += 2  # BSD sed's suffix
        elif a in takes or (not sed and a.startswith("-") and a.endswith("e")):  # perl -pe CODE
            script_given, i = True, i + 2
        else:
            if sed and a[:2] in ("-e", "-f") and len(a) > 2:  # -e'script' joined
                script_given = True
            elif not a.startswith("-"):
                ops.append(a)
            i += 1
    if not script_given:
        ops = ops[1:]
    return [o for o in ops if o]


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


def judge_command(command: str, cwd: str, agent: str | None = None) -> str | None:
    """The refusal for a Bash command that would change a held repo, or None. `agent` is the
    subagent's id (None on the main thread), which brings the loop queue's holds in."""
    if repo_hold is None:
        return None
    try:
        segments = _segments(command)
    except ValueError:
        return None
    here = cwd
    shell: dict[str, str] = {}  # the command's own assignments, in order (`S=dir; ... $S/f`)
    for raw in segments:
        assigned = _assigns(raw)
        if assigned is not None:
            shell.update({k: _expand(v, shell) or "" for k, v in assigned.items()})
            continue
        # Redirections first: `> path` writes path, whatever the command.
        seg: list[str] = []
        i = 0
        while i < len(raw):
            tok = raw[i]
            if tok in REDIRECTS or (tok.endswith(">") and tok.rstrip(">").isdigit()):
                dest = _expand(raw[i + 1], shell) if i + 1 < len(raw) else None
                if dest and raw[i + 1] != "&" and dest != "/dev/null":
                    for hold in _held(dest, here, False, agent):
                        return hold.describe(f"writing {dest} (a redirection)")
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
            to = _expand(args[0], shell, whole=True)
            here = os.path.join(here, os.path.expanduser(to)) if to and here else ""  # "" unknown
            continue
        if verb == "git":
            where, rest = _git_target(args, here)
            if rest and _git_writes(rest[0], rest[1:]):
                for hold in _held(where, here, False, None if rest[0] in GIT_BESIDE else agent):
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
        elif verb in IN_PLACE:
            targets = _in_place_targets(verb, args)
        elif verb == "find" and any(a in ("-delete", "-exec", "-execdir") for a in args):
            targets, recursive = [a for a in args if not a.startswith("-")][:1], True
        elif verb == "dd":
            targets = [a[3:] for a in args if a.startswith("of=")]
        for raw_target in targets:
            target = _expand(raw_target, shell)
            if not target:
                continue  # an empty operand, or one whose head is unexpandable: no knowable path
            for hold in _held(target, here, recursive, agent):
                return hold.describe(f"`{verb}` on {target}")
    return None


def judge_path(path: str, tool: str, cwd: str, agent: str | None = None) -> str | None:
    if repo_hold is None or not path:
        return None
    hold = repo_hold.find_hold(path, cwd)
    if hold is None and agent and queue_hold is not None:
        hold = queue_hold.find(path, cwd, agent)
    return hold.describe(f"{tool} of {path}") if hold else None


def judge_ci_poll(command: str, agent: str | None) -> str | None:
    """A subagent's direct gh CI poll, refused naming loopctl's door (LOOP.GHDOOR), or None."""
    if not agent:
        return None
    try:
        import ci_door  # beside this script; lazily, as it imports this module's parser
    except ImportError:
        return None
    calls = ci_door.calls_in(command)
    return ci_door.refusal(calls) if calls else None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()
    agent = str(event.get("agent_id") or "").strip() or None  # absent on the main thread
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        reason = judge_path(path, tool, cwd, agent)
    elif tool in SHELL_TOOLS:
        command = tool_input.get(COMMAND_FIELD) or ""
        reason = judge_ci_poll(command, agent) or judge_command(command, cwd, agent)
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
