"""What a process RUNS, read from `ps`: its CPU time, and the code a shell in it runs.

Split out of lib/load_reap.py (GOH.STRAYSIGNAL3, the file at its 500-line cap): the backstop's third
shape asks whether an orphan is burning a core on a busy loop, and these are the pure readers that
question rests on -- `ps`'s CPU-time format, and a command line's shell code (an inline `-c`
argument, or the script file it names), which lib/busyloop.py then reads as code. Nothing here
spawns, signals or decides; lib/load_reap.py does. Pinned by tests/test_load_burning.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# The shells whose code this rule reads. A process that is not one of these is not running shell
# code, and a shell loop is the shape.
SHELLS = ("sh", "bash", "zsh", "ksh", "dash")

# How much of a script the rule reads. A loader is small; a huge file is not read to the end for
# a loop that is in its first lines.
SCRIPT_BYTES = 65536


def cpu_seconds(text: str) -> float:
    """The CPU time `ps` reports, in seconds.

    `ps` prints it as `[[dd-]hh:]mm:ss[.ss]`; a day is 86400 seconds and an hour 3600, and a
    value this cannot read is zero -- a process whose CPU time is unknown is not burning.
    """
    text = text.strip()
    if not text:
        return 0.0
    seconds = 0.0
    if "-" in text:
        head, _, text = text.partition("-")
        try:
            seconds = float(head) * 86400
        except ValueError:
            return 0.0
    try:
        numbers = [float(part) for part in text.split(":")]
    except ValueError:
        return 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


PROC = Path("/proc")


def kernel_cpu_seconds(pid: int, proc: Path = PROC, ticks: int | None = None) -> float | None:
    """The kernel's CPU time for `pid` (utime + stime of `/proc/<pid>/stat`), or None where there
    is no /proc entry for it (macOS; a gone process).

    procps `ps -o cputime` prints WHOLE seconds, so on Linux the sweep's floor and its "still
    growing" sample read a sub-second burn as none (GOH.WORKERFAILS); BSD ps prints hundredths.
    The comm field is parenthesised and may hold spaces and `)`, so fields count from its LAST `)`.
    """
    try:
        text = (proc / str(pid) / "stat").read_text()
    except OSError:
        return None
    rest = text.rpartition(")")[2].split()
    try:
        used = int(rest[11]) + int(rest[12])  # fields 14 and 15: utime, stime
    except (IndexError, ValueError):
        return None
    return used / (ticks or os.sysconf("SC_CLK_TCK"))


def _words(command: str) -> list[tuple[str, int]]:
    """The command's words, and where each one starts."""
    out = []
    index = 0
    length = len(command)
    while index < length:
        if command[index].isspace():
            index += 1
            continue
        start = index
        while index < length and not command[index].isspace():
            index += 1
        out.append((command[start:index], start))
    return out


def _inline_code(command: str, at: int) -> str | None:
    """The text after a `-c`, as the shell will read it.

    The quotes come off once: the argument of `-c` IS code, and a loop inside it runs. What is
    inside THAT code is judged by the mask, so a loop the code only quotes is still text.
    """
    rest = command[at + 2 :].lstrip()
    if not rest:
        return None
    if rest[0] in "'\"":
        quote = rest[0]
        end = rest.find(quote, 1)
        return rest[1:end] if end > 0 else rest[1:]
    parts = rest.split()
    return parts[0] if parts else None


def shell_code(command: str) -> str | None:
    """The code a SHELL in this command line runs: an inline `-c` argument, or a script file.

    None when the command line runs no shell, or names no file this rule can read: a script whose
    directory was pruned is a spinner nobody can judge, and it is left alone.
    """
    words = _words(command)
    for index, (word, _at) in enumerate(words):
        if word.rsplit("/", 1)[-1] not in SHELLS:
            continue
        if index + 1 >= len(words):
            continue
        nxt, nxt_at = words[index + 1]
        if nxt == "-c":
            return _inline_code(command, nxt_at)
        try:
            return Path(nxt).read_text(encoding="utf-8", errors="replace")[:SCRIPT_BYTES]
        except OSError:
            return None
    return None
