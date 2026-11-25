"""A hand-written `until`/`while ... sleep` wait with no deadline (GOH.WAITDEADLINE, 2026-11-21).

THE CLASS. A coordinator's background `until <cond>; do sleep 20; done` had no deadline, and its
condition could never turn true (the harness grep's -v -q exit code, GOH.UGREPVQ): it ran 2 h 50 min
until the owner noticed. `goh wait` has a timeout (default 7200 s); a hand-written loop does not.

THE RULE. In the CODE of a Bash or Monitor command (quoted text, heredoc bodies and comments are
masked by `busyloop.mask`), a `while`/`until` loop whose body sleeps is refused unless bounded:

  * a `timeout N` / `gtimeout N` wrapper earlier in the command, or
  * a deadline check in the loop: `$SECONDS`, a `deadline`/`DEADLINE` variable, `date +%s`, an
    arithmetic `((...))` test or a `-lt`/`-le`/`-gt`/`-ge` comparison (a counter).

The refusal points at `goh wait` (pid, file, json, land, ship; each with `--timeout`). The escape
is `# wait-ok: <reason>`. A loop inside a quoted string (`timeout 600 bash -c '...'`) is text at
the top level, so the wrapped form passes.

A Bash `until`/`while` loop that sleeps with no `timeout N` wrapper and no deadline check
(`$SECONDS`, `deadline`, `date +%s`, `((..))`, `-lt`/`-ge`) is refused, naming `goh wait`
(GOH.WAITDEADLINE). Monitor commands are judged alike (GOH.MONGUARD). Escape `# wait-ok: <reason>`.
"""

from __future__ import annotations

import re

import busyloop

ESCAPE = re.compile(r"#\s*wait-ok:\s*\S")
_LOOP = re.compile(r"\b(?:until|while)\b")
_DONE = re.compile(r"\b(?:done|do|until|while|for)\b")
_SLEEP = re.compile(r"(?<![\w./-])sleep\b")
_TIMEOUT = re.compile(r"(?<![\w./-])g?timeout\s+(?:-\S+\s+)*\d")
_BOUND = re.compile(r"\bSECONDS\b|\bdeadline\b|\bDEADLINE\b|date\s+\+%s|\(\(|\s-(?:lt|le|gt|ge)\s")


def _loop_text(masked: str, start: int) -> str:
    """The masked text of the loop that starts at START, through its matching `done`."""
    depth = 0
    for match in _DONE.finditer(masked, start):
        word = match.group(0)
        if word in ("until", "while", "for"):
            depth += 1
        elif word == "done":
            depth -= 1
            if depth == 0:
                return masked[start : match.end()]
    return masked[start:]


def judge(command: str) -> str | None:
    """The refusal for a sleeping wait loop with no deadline, or None. See the module docstring."""
    if ESCAPE.search(command):
        return None
    masked = busyloop.mask(command)
    for match in _LOOP.finditer(masked):
        loop = _loop_text(masked, match.start())
        if not _SLEEP.search(loop) or _BOUND.search(loop):
            continue
        if _TIMEOUT.search(masked[: match.start()]):
            continue
        return (
            "Run instead: `goh wait pid|file|json|land|ship ... --timeout S` (default 7200 s). "
            "This `until`/`while` loop sleeps with no deadline, so a condition that can never "
            "turn true runs it forever: a coordinator's `until ... | grep -vq OLD; do sleep 20; "
            "done` ran 2 h 50 min on 2026-11-21. Or bound it: wrap it in `timeout N` "
            "(`timeout 600 bash -c 'until ...; do sleep 20; done'`) or check a deadline in the "
            "loop (`[ $SECONDS -lt 600 ]`). To keep it on purpose, add `# wait-ok: <reason>`."
        )
    return None
