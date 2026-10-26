"""The spinner's shape, ONE definition, for the three places that judge it.

THE CLASS (2026-10-21). The house had three ways to make load and one way to make a LEAK:
`tools/load.py` (registers its spinners and reaps them), a launchd backstop (kills REGISTERED
pids whose parent is 1), and a Claude Code guard that refused a Bash command spawning a
backgrounded busy loop. And yet, at 13:00 on 2026-10-21, twenty-four orphaned spinners were
burning four cores each -- `/bin/zsh /private/var/.../opencode/load.sh`, 78 minutes of CPU apiece,
four and a half hours old, parent 1, their owner long dead. The script was a hand-written file:

    for i in $(seq 1 24); do (while :; do :; done) & done

Three holes, and each needed closing at its own level: the guard could only see COMMANDS (not the
FILE that holds the loop), the backstop knew only the house's own spinner SHAPE (not a
hand-rolled one), and no gate at all looked at the files a repository tracks.

WHAT THIS MODULE IS. The rule the three levels share, written once:

  * a busy loop is a `while`/`until` whose body is nothing but `:`/`true`/`false` (or empty) --
    a loop that does no work, and therefore a spin. A loop that sleeps, waits or computes is not
    a spinner, and is not this class;
  * TEXT is not CODE. A loop inside a string, inside a heredoc body, or behind a `#` comment is
    TEXT: `echo 'while :; do :; done'` writes a loop, it does not run one. So the code is read
    with everything that is TEXT blanked out, and only what is left is judged;
  * a loop RUNS when the LINE it is on also carries a background operator (`&`). A foreground
    busy loop hangs the shell that runs it -- a different defect -- while a backgrounded one
    outlives it, re-parented to launchd, burning a core for hours.

The three levels that use this, and what each asks of it:

  * the Claude Code guard (`hooks/claude/discard_guard.py`) refuses a COMMAND or a FILE whose
    code carries a backgrounded busy loop;
  * the house check (`checks/check_busy_loop.py`) refuses a TRACKED FILE whose code carries one,
    and names `tools/load.py` as the way;
  * the backstop (`lib/load_reap.py`) asks a different question of the same shape: is this
    ORPHANED process burning a core on a loop like this one?

THE BUSY-WAIT (G16/G17, 2026-10-26) is the same class one step wider: a `while`/`until` of ANY
condition whose body does nothing (`until [ -f ready ]; do :; done`). It pins a core and hangs the
shell with no orphan needed, so it is wrong foreground or background. All three levels judge it
too: the guard for a command and for a file, and the check for a tracked file.

One definition, so the three cannot drift: a rule the guard applies and the check does not is
two rules, and the house has been bitten by exactly that (a checker that is written but not
wired is still gate source).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# THE PATTERNS live in `busyloop_shapes.json`, beside this file: ONE definition, read here with
# Python's `re` and by `crates/goh/src/busyloop.rs` (the tracked-file check) through `include_str!`
# and fancy-regex. Neither language carries a copy, so the guard, the check and the backstop cannot
# drift (G17, 2026-10-26: the busy-wait shape had been added to the Python half only).
_SHAPES = json.loads((Path(__file__).resolve().parent / "busyloop_shapes.json").read_text())

# A loop whose body is nothing but `:` (or `true`/`false`): the spinner shape. The body of a loop
# that sleeps, waits or does work is not a spinner, so it is not a finding.
BUSY_LOOP = re.compile(_SHAPES["busy_loop"])

# THE BUSY-WAIT (G16, 2026-10-26): a `while`/`until` of ANY condition whose body does nothing --
# `until [ -f ready ]; do :; done`. It re-tests the condition as fast as the CPU allows, so it pins
# a core until the condition holds (forever, if it never does) and hangs the shell meanwhile. It
# needs no background operator to be wrong, which is why it is not BUSY_LOOP (whose condition is
# `:`/`true` and which only leaks when backgrounded). The condition may not contain a `do`/`done`
# keyword (a wider reach would pair one loop's head with another loop's body), and a `read`
# condition is exempt: `while read -r l; do :; done < f` blocks on its input and drains it.
BUSY_WAIT = re.compile(_SHAPES["busy_wait"])
READ_CONDITION = re.compile(_SHAPES["read_condition"])

# The shells whose files this shape is read in. A file that is not one of these is not judged:
# the shape is a SHELL construct, and a rule that reads a Python file's text for shell loops
# would refuse a fixture that merely PLANTS the form. The same list the native tier uses
# (`crate::shellsrc`), so the two tiers judge the same file the same way.
SHELL_SUFFIXES = (".sh", ".sh.tmpl", ".bash", ".zsh")
SHELL_SHEBANGS = ("sh", "bash", "zsh", "ksh", "dash")


def _blank(out: list[str], start: int, end: int) -> None:
    """Blank a span, keeping the length: a match keeps the offset it had."""
    for index in range(start, min(end, len(out))):
        if out[index] != "\n":
            out[index] = " "


def _end_of_double(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == '"':
            return index + 1
        index += 1
    return len(text)


def _mask_heredoc(text: str, out: list[str], start: int) -> int:
    """Blank a heredoc's body: the text after `<<WORD` until the line that ends it.

    A heredoc that plants the raw form in a fixture is TEXT: a rule must not refuse the writer
    of a test that proves the rule refuses it.
    """
    index = start + 2
    if index < len(text) and text[index] == "-":
        index += 1
    quote = ""
    if index < len(text) and text[index] in "'\"":
        quote = text[index]
        index += 1
    begin = index
    while index < len(text) and text[index] not in " \t\n;&|<>()":
        index += 1
    delimiter = text[begin:index]
    if quote and index < len(text) and text[index] == quote:
        index += 1
    if not delimiter:
        return index
    line_end = text.find("\n", index)
    if line_end < 0:
        return len(text)
    body = line_end + 1
    while body <= len(text):
        line_end = text.find("\n", body)
        stop = len(text) if line_end < 0 else line_end
        _blank(out, body, stop)
        if text[body:stop].strip() == delimiter:
            return stop
        if line_end < 0:
            return len(text)
        body = line_end + 1
    return len(text)


def mask(text: str) -> str:
    """The text with everything that is TEXT blanked: quoted spans, heredoc bodies, comments.

    The length is unchanged, so a match keeps the offset it had in the text.
    """
    out = list(text)
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = text.find("'", index + 1)
            end = length if end < 0 else end + 1
            _blank(out, index, end)
            index = end
            continue
        if char == '"':
            end = _end_of_double(text, index)
            _blank(out, index, end)
            index = end
            continue
        if text.startswith("<<", index):
            index = _mask_heredoc(text, out, index)
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t\n;&|(<>"):
            end = text.find("\n", index)
            end = length if end < 0 else end
            _blank(out, index, end)
            index = end
            continue
        index += 1
    return "".join(out)


def _background_in(text: str) -> bool:
    """Is there a background operator in this text?

    `&&` is a conjunction and `>&` is a redirect: neither starts a background job, and both are
    common enough that reading them as one would refuse ordinary commands.
    """
    for index, char in enumerate(text):
        if char != "&":
            continue
        before = text[index - 1] if index else ""
        after = text[index + 1] if index + 1 < len(text) else ""
        if before in ("&", ">") or after == "&":
            continue
        return True
    return False


def _line_of(text: str, offset: int) -> tuple[int, int]:
    """(start, end) of the line carrying `offset`."""
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    end = len(text) if end < 0 else end
    return start, end


def busy_loops(text: str) -> list[re.Match[str]]:
    """Every busy loop in the CODE of this text (the text's TEXT is masked out first)."""
    return list(BUSY_LOOP.finditer(mask(text)))


def has_busy_loop(text: str) -> bool:
    """Is there a busy loop in the code of this text, foreground or background?

    The BACKSTOP's question: a loop like this, orphaned, burns a core however it was started.
    """
    return bool(busy_loops(text))


def spins(text: str) -> bool:
    """Does this text RUN a busy loop in the background?

    The GUARD's and the CHECK's question: the loop has to be in code (not in a string, a
    heredoc body or a comment), and the line carrying it has to carry a background operator.
    """
    code = mask(text)
    for match in BUSY_LOOP.finditer(code):
        start, end = _line_of(code, match.start())
        if _background_in(code[start:end]):
            return True
    return False


def busy_waits(text: str) -> list[re.Match[str]]:
    """Every busy-wait in the CODE of this text (quoted spans, heredoc bodies and comments are
    masked out first), foreground or background, `read` drains excepted."""
    return [
        match
        for match in BUSY_WAIT.finditer(mask(text))
        if not READ_CONDITION.match(match.group("cond"))
    ]


def waits(text: str) -> bool:
    """Does this text RUN a busy-wait? The tool-boundary guard's question for a COMMAND: the
    loop need not be backgrounded, because it is wrong on its own (a pinned core, a hung shell)."""
    return bool(busy_waits(text))


def is_shell(path: str, text: str) -> bool:
    """Is this file a shell script, so the shape is CODE in it?

    The path's suffix, or the shebang on its first line. A file that is neither is not judged:
    the shape is a shell construct, and a rule that reads a Python file for shell loops refuses
    the fixture that plants the form.
    """
    if path.endswith(SHELL_SUFFIXES):
        return True
    first = text.split("\n", 1)[0]
    if not first.startswith("#!"):
        return False
    words = first[2:].split()
    if not words:
        return False
    if words[0].rsplit("/", 1)[-1] == "env" and len(words) > 1:
        words = words[1:]
    name = words[0].rsplit("/", 1)[-1]
    return name in SHELL_SHEBANGS


# The per-line escape: `# load-ok: <reason>`, on the line or on the comment lines above it.
MARKER = "load-ok:"


def marker_reason(line: str) -> str | None:
    """The reason on this line's marker, or None when the line has no marker.

    A marker is a COMMENT: the text before the `#` is not part of it, so a line that merely
    mentions `load-ok:` in code is not an escape.
    """
    hash_at = line.find("#")
    if hash_at < 0:
        return None
    at = line[hash_at:].find(MARKER)
    if at < 0:
        return None
    return line[hash_at + at + len(MARKER) :].strip()


def markers(text: str) -> list[tuple[int, str]]:
    """Every marker in this text: `(line, reason)`."""
    out = []
    for index, line in enumerate(text.split("\n"), start=1):
        reason = marker_reason(line)
        if reason is not None:
            out.append((index, reason))
    return out


def excusing_marker(text: str, line: int) -> int | None:
    """The line of the marker that excuses the finding at `line`, or None.

    The marker may be on the line itself, or on the comment lines DIRECTLY above it -- the house's
    convention for a per-line escape (`goh secrets`, `goh moving-tags`). A marker further up,
    past a line of code, is a marker about something else.
    """
    lines = text.split("\n")
    own = lines[line - 1] if 0 < line <= len(lines) else ""
    reason = marker_reason(own)
    if reason is not None:
        return line if reason else None
    index = line
    while index >= 2:
        current = lines[index - 2].strip()
        if not current.startswith("#"):
            return None
        reason = marker_reason(current)
        if reason is not None:
            return (index - 1) if reason else None
        index -= 1
    return None


def _line_numbers(text: str, matches: list[re.Match[str]]) -> set[int]:
    return {text[: match.start()].count("\n") + 1 for match in matches}


def unexcused_spinners(text: str) -> list[int]:
    """The lines of the BACKGROUNDED busy loops this text runs that NO marker excuses."""
    code = mask(text)
    found = set()
    for match in BUSY_LOOP.finditer(code):
        start, end = _line_of(code, match.start())
        if _background_in(code[start:end]):
            found |= _line_numbers(text, [match])
    return [line for line in sorted(found) if excusing_marker(text, line) is None]


def unexcused_waits(text: str) -> list[int]:
    """The lines of the busy-WAITS this text runs that NO marker excuses (G17), foreground or not."""
    found = _line_numbers(text, busy_waits(text))
    return [line for line in sorted(found) if excusing_marker(text, line) is None]


def unexcused(text: str) -> list[int]:
    """The lines of the busy loops and busy-waits this text runs that NO marker excuses.

    The check's question and the guard's for a FILE: a loop that runs and nothing excuses is the
    leak (a backgrounded spinner) or the pinned core (a busy-wait, foreground or not). A line
    carrying both shapes (`while :; do :; done &` is both) is one finding.
    """
    return sorted(set(unexcused_spinners(text)) | set(unexcused_waits(text)))
