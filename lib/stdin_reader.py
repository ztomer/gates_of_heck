"""A Bash command whose stdin reader has no input is refused before it runs (GOH.STDINHANG).

THE CASE (2026-11-15). The sys_updater agent ran

    sed ...; cat > /tmp/x.py 2>/dev/null; python3 - $S <<'EOF' ... EOF

The `cat` meant to write a file had no input of its own, so it read the harness's stdin, which never
closes: it hung 5h51m, the agent had long finished, and its task stayed "running" until the
coordinator killed the `cat`.

WHAT THE HARNESS FEEDS, probed on this Mac (Claude Code 2.1.325, foreground and background): the
Bash tool runs `eval '<command>' < /dev/null`, so a reader with no input reads an empty file and
writes nothing -- UNLESS the command holds a heredoc, when the `< /dev/null` is left off and fd 0 is
the harness's own unix socket, which never reaches end of file. Either way the reader is a defect:
an empty file, or a hang no timeout ends.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`: quoted text, heredoc bodies and
comments are not commands). A READER is `cat` with no file operand (or `-`), `tee`, `read` (not
`-t`, not `-u`), `python`/`python3` with `-` or no script, and a shell that runs its stdin as
its script (`sh -s`, a bare `bash`). It is FED by a heredoc, a here-string, a `<` redirect or a pipe
into that same command -- or into the compound command around it (`cmd | while read`, `done < f`,
`{ cat; } < f`) -- or by running in the background (a background list reads /dev/null). A reader
under `timeout` is bounded and runs. One fed or bounded reader beside an unfed one does not excuse
it. The escape is `# stdin-ok: <reason>`.

The detector for the readers already hung is `hooks/claude/stdin_hangs.py` (`goh agent-stalls`),
and it asks `reads_stdin` the same question of a live process's argv: one definition of a reader.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import shell_segments

ESCAPE = re.compile(r"#\s*stdin-ok:\s*\S")
REDIRECT = re.compile(r"^(\d*|&)(>>|>\||>&|>|<<<|<>|<&|<)(.*)$")
STDIN_OPS = {"<", "<>", "<&", "<<<"}
PYTHON = re.compile(r"^python(\d+(\.\d+)*)?$")
TIMEOUTS = {"timeout", "gtimeout"}
OPENERS = {"while": "done", "until": "done", "if": "fi", "for": "done", "select": "done", "{": "}"}
SKIP = {"do", "then", "else", "elif", "!", "time"}
CLOSERS = {"done", "fi", "}"}
WAY_OUT = (
    "Its input is the harness's stdin: the Bash tool feeds `< /dev/null` (the reader writes "
    "nothing) unless the command holds a heredoc, and then fd 0 is the harness's socket, which "
    "never closes -- a `cat > /tmp/x.py` beside a `python3 - <<'EOF'` hung 5h51m on 2026-11-15, "
    "its agent long finished (GOH.STDINHANG). Feed the reader what it should read: a heredoc on "
    "that command (`cat > f <<'EOF' ... EOF`), a here-string, a pipe, or `< file`; to make an "
    "empty file, `: > f`. To read the harness's stdin on purpose, add `# stdin-ok: <reason>`."
)


@dataclass
class _Frame:
    closer: str
    fed: bool = False


def _redirect(tok: str) -> tuple[str, str] | None:
    """(operator, glued target) of a redirection token, or None for a word (`<(cmd)` is a word)."""
    m = REDIRECT.match(tok)
    if m is None or m.group(3).startswith("("):
        return None
    return m.group(2), m.group(3)


def _words(tokens: list[str], bodies: dict[str, str]) -> tuple[list[str], bool]:
    """(argv, fed): the words a simple command runs, and whether a redirection feeds its stdin."""
    argv: list[str] = []
    fed = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if tok in bodies:
            fed = True
            continue
        red = _redirect(tok)
        if red is None:
            argv.append(tok)
            continue
        op, target = red
        fed = fed or op in STDIN_OPS
        if not target and i < len(tokens) and tokens[i] not in bodies:
            i += 1  # the target is the next word
    return argv, fed


def reads_stdin(argv: list[str]) -> str | None:
    """The reader an argv is, when it reads its stdin, or None. One definition: the Bash guard asks
    it of a command's words, the stall detector of a live process's argv."""
    prog, args = shell_segments.program(argv)
    prefix = argv[: len(argv) - len(args) - 1]
    if any(os.path.basename(t) in TIMEOUTS for t in prefix):
        return None  # bounded: it ends on its own
    if prog == "cat":
        operands, ended = [], False
        for a in args:
            if ended or a == "-" or not a.startswith("-"):
                operands.append(a)
            ended = ended or a == "--"
        return "cat" if not operands or "-" in operands else None
    if prog == "tee":
        return None if {"--help", "--version"} & set(args) else "tee"
    if prog == "read":
        flags = "".join(a[1:] for a in args if a.startswith("-"))
        return None if "t" in flags or "u" in flags else "read"
    if PYTHON.match(prog):
        skip = False
        for a in args:
            if skip:
                skip = False
            elif a == "-":
                return f"{prog} -"
            elif a[:2] in ("-c", "-m") or a in ("-V", "--version", "-h", "--help"):
                return None
            elif a in ("-W", "-X"):
                skip = True
            elif not a.startswith("-"):
                return None  # a script file
        return prog
    if prog in shell_segments.SHELLS and not {"--version", "--help"} & set(args):
        return f"{prog} -s" if shell_segments.reads_script(argv) else None
    return None


def _close(tok: str) -> tuple[str, int]:
    """(the word, the number of subshell `)` closing after it): `cat)` closes one."""
    n = 0
    while tok.endswith(")") and tok.count("(") < tok.count(")"):
        tok, n = tok[:-1], n + 1
    return tok, n


def _unfed(command: str) -> str | None:
    """The first simple command that reads stdin with nothing feeding it, or None.

    Raises ValueError where the command cannot be split; returns None where its compound commands
    do not balance (a function body, a `case`): unjudged, never refused on a guess.
    """
    segs, bodies = shell_segments.split_ops(command)
    stack: list[_Frame] = []
    found: list[tuple[list[str], str, list[_Frame]]] = []
    prev = ""
    exec_fed = False  # `exec < f`: every later reader reads f
    for tokens, op in segs:
        piped = prev in ("|", "|&")
        prev = op
        words = list(tokens)
        opened: list[_Frame] = []
        while words:
            w = words[0]
            if w == "case" or w.startswith("((") or w.endswith("()"):
                return None
            if w in OPENERS:
                opened.append(_Frame(OPENERS[w]))
                if w in ("for", "select"):
                    words = []
                    break
            elif w.startswith("(") and not w.startswith("$("):
                opened.append(_Frame(")"))
                words[0] = w[1:]
                if not words[0]:
                    words.pop(0)
                continue
            elif w not in SKIP:
                break
            words.pop(0)
        if opened and piped:
            opened[0].fed = True
        stack += opened
        if words and words[0] in CLOSERS:
            if not stack or stack[-1].closer != words[0]:
                return None
            frame = stack.pop()
            frame.fed = frame.fed or _words(words[1:], bodies)[1] or op == "&"
            continue
        closes, cmd, rest = 0, words, []
        for i, tok in enumerate(words):
            word, closes = _close(tok)
            if closes:
                cmd, rest = [*words[:i], word] if word else words[:i], words[i + 1 :]
                break
        argv, fed = _words(cmd, bodies)
        fed = fed or exec_fed or (piped and not opened) or (op == "&" and not closes)
        reader = reads_stdin(argv) if argv else None
        if reader and not fed:
            found.append((tokens, reader, list(stack)))
        exec_fed = exec_fed or (argv == ["exec"] and fed)
        for _ in range(closes):
            if not stack or stack[-1].closer != ")":
                return None
            frame = stack.pop()
            frame.fed = frame.fed or _words(rest, bodies)[1] or op == "&"
            rest = []
    if stack:
        return None
    for tokens, reader, chain in found:
        if not any(f.fed for f in chain):
            shown = " ".join(t for t in tokens if t not in bodies)
            return f"`{shown}` reads stdin ({reader}) and nothing feeds it"
    return None


def judge(command: str) -> str | None:
    """The refusal for a stdin reader with no input, or None."""
    if ESCAPE.search(command):
        return None
    try:
        hit = _unfed(command)
    except ValueError:
        return None
    return (
        f"{hit}: no heredoc, here-string, pipe or `<` on that command. {WAY_OUT}" if hit else None
    )


__all__ = ["ESCAPE", "judge", "reads_stdin"]
