#!/usr/bin/env python3
"""A Claude Code PreToolUse hook (Bash): refuse the two commands that cost the estate most.

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

It judges only what it can parse. A command it cannot split runs, unjudged: the guard must never
be the thing that breaks an ordinary command. A pattern inside a string or a heredoc is TEXT, not
code, so it is not a finding: `echo 'while :; do :; done'` writes a loop, it does not run one.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

ESCAPE = re.compile(r"#\s*discard-ok:\s*\S")
LOAD_ESCAPE = re.compile(r"#\s*load-ok:\s*\S")
SEPARATORS = {"&&", "||", ";", "|", "&"}

# A loop whose body is nothing but `:` (or `true`): the spinner shape. The body of a loop that
# sleeps, waits or does work is not a spinner, so it is not a finding.
BUSY_LOOP = re.compile(
    r"\b(?:while|until)\s+(?::|true|false)\s*;?\s*do\s*(?::|true|false)?\s*;?\s*done\b"
)


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


def _blank(out: list[str], start: int, end: int) -> None:
    for index in range(start, min(end, len(out))):
        if out[index] != "\n":
            out[index] = " "


def _end_of_double(command: str, start: int) -> int:
    index = start + 1
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == '"':
            return index + 1
        index += 1
    return len(command)


def _mask_heredoc(command: str, out: list[str], start: int) -> int:
    """Blank a heredoc's body: the text after `<<WORD` until the line that ends it.

    A heredoc that plants the raw form in a fixture is TEXT: the guard must not refuse the writer
    of a test that proves the guard refuses it.
    """
    index = start + 2
    if index < len(command) and command[index] == "-":
        index += 1
    quote = ""
    if index < len(command) and command[index] in "'\"":
        quote = command[index]
        index += 1
    begin = index
    while index < len(command) and command[index] not in " \t\n;&|<>()":
        index += 1
    delimiter = command[begin:index]
    if quote and index < len(command) and command[index] == quote:
        index += 1
    if not delimiter:
        return index
    line_end = command.find("\n", index)
    if line_end < 0:
        return len(command)
    body = line_end + 1
    while body <= len(command):
        line_end = command.find("\n", body)
        stop = len(command) if line_end < 0 else line_end
        _blank(out, body, stop)
        if command[body:stop].strip() == delimiter:
            return stop
        if line_end < 0:
            return len(command)
        body = line_end + 1
    return len(command)


def _mask_quoted(command: str) -> str:
    """The command with quoted text and heredoc bodies blanked.

    A pattern inside a string is TEXT, not code. The length is unchanged, so a match keeps the
    offset it had in the command.
    """
    out = list(command)
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = command.find("'", index + 1)
            end = length if end < 0 else end + 1
            _blank(out, index, end)
            index = end
            continue
        if char == '"':
            end = _end_of_double(command, index)
            _blank(out, index, end)
            index = end
            continue
        if command.startswith("<<", index):
            index = _mask_heredoc(command, out, index)
            continue
        index += 1
    return "".join(out)


def _backgrounded(code: str) -> bool:
    """Is there a background operator in the code?

    `&&` is a conjunction and `>&` is a redirect: neither starts a background job, and both are
    common enough that reading them as one would refuse ordinary commands.
    """
    for index, char in enumerate(code):
        if char != "&":
            continue
        before = code[index - 1] if index else ""
        after = code[index + 1] if index + 1 < len(code) else ""
        if before in ("&", ">") or after == "&":
            continue
        return True
    return False


def judge_load(command: str) -> str | None:
    """The refusal reason for a command that spawns a busy loop, or None when it runs.

    The loop must be BACKGROUNDED: a foreground busy loop dies with the shell that runs it (it
    hangs that shell, which is a different defect), while a backgrounded one outlives it.
    """
    if LOAD_ESCAPE.search(command):
        return None
    code = _mask_quoted(command)
    if not BUSY_LOOP.search(code):
        return None
    if not _backgrounded(code):
        return None
    return (
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


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (event.get("tool_input") or {}).get("command") or ""
    reason = judge(command, event.get("cwd") or os.getcwd()) or judge_load(command)
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
