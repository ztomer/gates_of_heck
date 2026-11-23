"""An escape that comments out the code after it is refused (GOH.ADMITOKLINE).

THE CASE (2026-11-23). A lane wrote `cargo build # admit-ok: one probe; git restore x`. Every
`# x-ok: <reason>` escape a Bash guard reads (`admit-ok`, `quit-ok`, `discard-ok`, `load-ok`,
`secrets-ok`, `kill-ok`, `reset-ok`, ...) is a shell COMMENT, so the shell ran `cargo build` and
nothing else: the restore step after the escape, on its line, was the comment's text and silently
never ran. The guard that read the escape admitted the command, and nothing said what was lost.

THE RULE, for every such escape at once (the class, not one guard's regex): an escape that opens a
comment in the CODE of a command (`busyloop.mask`: not inside quotes, a heredoc body or an earlier
comment) and is followed, on its own line, by a shell operator (`;`, `&&`, `||`, `|`, a `&` that is
not inside a word) is refused, naming the text the comment swallowed. The escape last on its line,
or the rest of the command on a line of its own, admits as before. Run first by the Bash guard
(hooks/claude/discard_guard.py), ahead of every rule an escape would excuse.
Pinned by tests/test_escape_line.py.
"""

from __future__ import annotations

import re

import busyloop

ESCAPE = re.compile(r"#[ \t]*([a-z][a-z0-9]*(?:-[a-z0-9]+)*-ok):")
# A shell operator in the comment's text: what follows it was meant to run. A lone `&` glued to a
# word on both sides (`R&D`) is prose, never a background operator.
OPERATOR = re.compile(r"&&|\|\||[;|]|(?<!\w)&|&(?!\w)")
OPENS_COMMENT = " \t\n;&|()"
REASON = (
    "`# {name}:` is a shell comment, so it comments out the rest of its line: `{swallowed}` "
    "would never run, and the guard the escape excuses would admit the command as if it had "
    "(GOH.ADMITOKLINE: a lane's restore step after `# admit-ok:` silently did not run). Put the "
    "escape LAST on its line, or the rest of the command on a line of its own."
)


def _in_code(command: str, at: int) -> bool:
    """Is offset AT in the command's code (not in quotes, a heredoc body or a comment)?"""
    return busyloop.mask(command[:at] + "X")[-1] == "X"


def swallowed(command: str) -> list[tuple[str, str]]:
    """Each escape whose comment swallows code: (its name, the swallowed text from the operator)."""
    out: list[tuple[str, str]] = []
    for m in ESCAPE.finditer(command):
        at = m.start()
        if at and command[at - 1] not in OPENS_COMMENT:
            continue  # `a#b-ok:` is a word, no comment
        if not _in_code(command, at):
            continue
        end = command.find("\n", m.end())
        rest = command[m.end() : len(command) if end < 0 else end]
        if op := OPERATOR.search(rest):
            out.append((m.group(1), rest[op.start() :].strip()))
    return out


def judge(command: str) -> str | None:
    """The refusal for a command whose escape comments out code after it, or None."""
    found = swallowed(command)
    if not found:
        return None
    name, text = found[0]
    return REASON.format(name=name, swallowed=text)
