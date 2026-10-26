#!/usr/bin/env python3
"""A Claude Code PreToolUse hook: refuse the three things that cost the estate most.

THE DISCARD (v0.30.0). `git checkout -- <file>`, `git restore <file>`, `git reset --hard`, `git
checkout -f` and `git clean -f` discard worktree changes that no reflog keeps. The calibration loop
(break the code, watch the test go red, restore it) reaches for the first of them, and on a file
that held other uncommitted work it destroyed that work -- twice in one session, with the lesson
already written down in two skills (tests/test_claude_discard_guard.py). So the system says no
instead: when the command's scope holds uncommitted changes, it is refused with the files that
would be lost and the ways forward. A deliberate discard says so in the command:
`# discard-ok: <reason>`.

THE LOAD (v0.44.0). A command that spawns a backgrounded busy loop (`while :; do :; done &`) is
refused too. A busy loop has nothing to reap it: when the shell that owns it is killed, the
spinner is re-parented to launchd (ppid 1) and keeps burning a core for hours -- 24 of them for
3h12m at a load average of 206, then 12 for 26m, then 8 for 28m inside the gates themselves, three
times in one day, each blamed on "other agents". The house has ONE way to make load
(`$GOH_DIR/tools/load.py`), which registers the pids it starts, kills exactly those on the way
out, and leaves a registry a launchd backstop reads when even that cannot run. A deliberate
background busy loop says so in the command: `# load-ok: <reason>`.

THE LOAD, IN A FILE (2026-10-21). The guard above saw COMMANDS only, and a command is not how the
loop usually arrives: at 13:00 on 2026-10-21 there were twenty-four orphaned spinners burning four
cores each -- `/bin/zsh /private/var/.../opencode/load.sh`, 78 minutes of CPU apiece, four and a
half hours old, parent 1, their owner long dead -- and the script was a hand-written FILE:

    for i in $(seq 1 24); do (while :; do :; done) & done

So the guard reads the WRITE path too: a file whose CODE runs a backgrounded busy loop is refused,
with the same way forward (`tools/load.py`) and the same escape (`# load-ok: <reason>`, in the
file, on the line or on the comment lines directly above it).

It judges only what it can parse. A command it cannot split runs, unjudged: the guard must never
be the thing that breaks an ordinary command. A pattern inside a string or a heredoc is TEXT, not
code, so it is not a finding: `echo 'while :; do :; done'` writes a loop, it does not run one. And
a FILE that is not a shell script is not judged at all: the shape is a shell construct.

THE SHAPE IS ONE DEFINITION (`lib/busyloop.py`), shared with the backstop that kills orphaned
spinners and with the house check that refuses a tracked file carrying one: a rule the guard
applies and the check does not is two rules.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# The house's ONE definition of the spinner's shape. It sits beside the guard, so the import is
# local; a missing one must not turn the guard into a wall, so it fails OPEN and says so.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
try:
    import busyloop
except ImportError:  # the shared rule is not there: the guard judges what it can
    busyloop = None
    print(
        "· discard guard: lib/busyloop.py is not there -- the load rule is not applied",
        file=sys.stderr,
    )

# The house's ONE definition of "background priority" (G1f), shared with tools/load.py, which
# refuses the same command line when it runs; the guard refuses it before a tool call is spent.
try:
    import load_priority
except ImportError:
    load_priority = None

ESCAPE = re.compile(r"#\s*discard-ok:\s*\S")
LOAD_ESCAPE = re.compile(r"#\s*load-ok:\s*\S")
SEPARATORS = {"&&", "||", ";", "|", "&"}

# The refusal a command earns: the way forward, and the one escape.
LOAD_REASON = (
    "This command spawns a busy loop with nothing to reap it (`while :; do :; done &`). "
    "When the shell that owns it is killed, the spinner is re-parented to launchd and keeps "
    "burning a core for hours: 24 of them for 3h12m at a load average of 206, then 12 for "
    "26m, then 8 for 28m inside the gates themselves, three times in one day, each blamed on "
    '"other agents". Make the load the house way, which reaps itself: '
    '`python3 "$GOH_DIR/tools/load.py" 8 -- <your command>` -- it registers the pids it '
    "starts, kills exactly those on the way out, refuses a second experiment while one holds "
    "the slot, and a launchd backstop kills any orphan that outlives its owner. To background "
    "a busy loop on purpose, add `# load-ok: <reason>` to the command."
)


def _new_text(tool_input: dict) -> str:
    """The text a WRITE introduces: the file's content, or what an edit adds.

    An edit's whole file is not in the event -- only the text it adds -- so that is what is
    judged: the loop a write introduces is the one the write's text carries.
    """
    content = tool_input.get("content")
    if isinstance(content, str):
        return content
    new_string = tool_input.get("new_string")
    if isinstance(new_string, str):
        return new_string
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        return "\n".join(
            str(edit.get("new_string", "")) for edit in edits if isinstance(edit, dict)
        )
    return ""


def _segments(command: str) -> list[list[str]]:
    """The command's simple commands, split on shell separators (the punctuation shlex keeps)."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = "#"
    segs: list[list[str]] = [[]]
    for tok in lex:
        if tok in SEPARATORS or set(tok) <= set(";&|"):
            segs.append([])
        else:
            segs[-1].append(tok)
    return [s for s in segs if s]


def _git(cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", cwd, *args], capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL, timeout=10, check=False,
    )  # fmt: skip


def _is_rev(cwd: str, tok: str) -> bool:
    return _git(cwd, "rev-parse", "--verify", "-q", f"{tok}^{{commit}}").returncode == 0


def _scope(cwd: str, argv: list[str]) -> tuple[str, list[str]] | None:
    """(kind, pathspecs) a git discard would touch, or None when this git command discards nothing.

    kind is "tracked" (modified tracked files; empty pathspecs = the whole tree) or "untracked".
    """
    sub, rest = argv[0], argv[1:]
    flags = [a for a in rest if a.startswith("-")]
    if sub == "checkout":
        if any(f in flags for f in ("-b", "-B", "--orphan", "-p", "--patch")):
            return None
        if "--" in rest:
            paths = rest[rest.index("--") + 1 :]
            return ("tracked", paths) if paths else None
        words = [a for a in rest if not a.startswith("-")]
        if words and _is_rev(cwd, words[0]):
            words = words[1:]
            if not words:  # a branch switch: it discards only when forced
                return ("tracked", []) if {"-f", "--force"} & set(flags) else None
        return ("tracked", words) if words else None
    if sub == "restore":
        if "--staged" in flags and "--worktree" not in flags and "-W" not in flags:
            return None
        paths = [a for a in rest if not a.startswith("-") and a != "--"]
        return ("tracked", paths) if paths else None
    if sub == "reset" and "--hard" in flags:
        return ("tracked", [])
    if sub == "switch" and {"-f", "--force", "--discard-changes"} & set(flags):
        return ("tracked", [])
    if sub == "clean":
        short = "".join(f[1:] for f in flags if not f.startswith("--"))
        if ("f" not in short and "--force" not in flags) or "n" in short or "--dry-run" in flags:
            return None
        return ("untracked", [a for a in rest if not a.startswith("-") and a != "--"])
    return None


def _at_risk(cwd: str, kind: str, paths: list[str]) -> list[str]:
    if _git(cwd, "rev-parse", "--git-dir").returncode != 0:
        return []
    if kind == "untracked":
        out = _git(cwd, "ls-files", "--others", "--exclude-standard", "--", *paths).stdout
    else:
        out = _git(cwd, "diff", "--name-only", "HEAD", "--", *paths).stdout
    return [ln for ln in out.splitlines() if ln.strip()]


def judge(command: str, cwd: str) -> str | None:
    """The refusal reason, or None when the command runs."""
    if ESCAPE.search(command):
        return None
    try:
        segments = _segments(command)
    except ValueError:
        return None
    here = cwd
    for seg in segments:
        if seg[0] == "cd" and len(seg) == 2:
            here = os.path.join(here, os.path.expanduser(seg[1]))
            continue
        if seg[0] != "git":
            continue
        args, where = seg[1:], here
        while args[:1] in (["-C"], ["-c"]) and len(args) > 1:  # git's own options, then the verb
            if args[0] == "-C":
                where = os.path.join(where, os.path.expanduser(args[1]))
            args = args[2:]
        if not args:
            continue
        scope = _scope(where, args)
        if scope is None:
            continue
        lost = _at_risk(where, *scope)
        if lost:
            shown = ", ".join(lost[:8]) + (f" (+{len(lost) - 8} more)" if len(lost) > 8 else "")
            return (
                f"`{' '.join(seg)}` would throw away uncommitted work in {where}: {shown}. "
                "No reflog keeps it. To keep it, commit a WIP first (the restore then goes back to "
                "that), or undo a deliberate break with an inverse edit. To discard it on purpose, "
                "add `# discard-ok: <reason>` to the command."
            )
    return None


def judge_load(command: str) -> str | None:
    """The refusal reason for a command that spawns a busy loop, or None when it runs.

    The loop must be BACKGROUNDED: a foreground busy loop dies with the shell that runs it (it
    hangs that shell, which is a different defect), while a backgrounded one outlives it.
    """
    if LOAD_ESCAPE.search(command):
        return None
    if busyloop is None or not busyloop.spins(command):
        return None
    return LOAD_REASON


def judge_priority(command: str) -> str | None:
    """The refusal for a `tools/load.py` command line whose wrapped command runs at background
    priority (`load.py 32 -- taskpolicy -b loop.sh`, the 10h14m run of 2026-10-25), or None."""
    if load_priority is None:
        return None
    try:
        segs = _segments(command)
    except ValueError:
        return None
    for seg in segs:
        for index, tok in enumerate(seg):
            if os.path.basename(tok) == "load.py":
                reason = load_priority.judge_invocation(seg[index + 1 :])
                if reason:
                    return f"`tools/load.py`: {reason}"
                break
    return None


def judge_file(path: str, content: str) -> str | None:
    """The refusal reason for a file that runs a backgrounded busy loop, or None.

    The FILE is the natural way to smuggle the loop in: the guard that saw only commands let
    twenty-four spinners through on 2026-10-21, and the loop arrived in a file.

    A file that is not a shell script is not judged: the shape is a shell construct, and a rule
    that reads a Python file's text for shell loops refuses the fixture that plants the form.
    """
    if busyloop is None:
        return None
    if not busyloop.is_shell(path, content):
        return None
    lines = busyloop.unexcused(content)
    if not lines:
        return None
    where = ", ".join(str(line) for line in lines[:6])
    return (
        f"{path}: {where}: this file runs a busy loop with nothing to reap it "
        "(`while :; do :; done &`). When the shell that owns it is killed, the spinner is "
        "re-parented to launchd and keeps burning a core for hours: 24 of them for 3h12m at a "
        "load average of 206, then 12 for 26m, then 8 for 28m inside the gates themselves, three "
        'times in one day, each blamed on "other agents" -- and on 2026-10-21 twenty-four of them '
        "were found that way, four cores each, four and a half hours old. Make the load the house "
        'way, which reaps itself: `python3 "$GOH_DIR/tools/load.py" 8 -- <your command>` -- it '
        "registers the pids it starts, kills exactly those on the way out, refuses a second "
        "experiment while one holds the slot, and a launchd backstop kills any orphan that "
        "outlives its owner. To background a busy loop on purpose, put `# load-ok: <reason>` on "
        "the line (or on the comment lines directly above it)."
    )


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get("tool_name") or ""
    tool_input = event.get("tool_input") or {}
    if tool in ("Write", "Edit", "MultiEdit"):
        reason = judge_file(tool_input.get("file_path") or "", _new_text(tool_input))
    else:
        command = tool_input.get("command") or ""
        reason = (
            judge(command, event.get("cwd") or os.getcwd())
            or judge_load(command)
            or judge_priority(command)
        )
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
