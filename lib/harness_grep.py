"""`grep -v -q` on the harness's grep, whose exit code is wrong (GOH.UGREPVQ, 2026-11-21).

THE CLASS. Claude Code (2.1.330) defines `grep` in every tool shell as a function that runs its
embedded ugrep (`exec -a ugrep <claude binary> -G ...`). That ugrep exits 1 for `-v -q` (any
spelling: `-vq`, `-qv`, `-v -q`, `--invert-match --quiet`) when a non-matching line EXISTS, where
POSIX grep exits 0: `printf 'a\\nb\\n' | grep -vq a; echo $?` prints 1 there and 0 for
`/usr/bin/grep`. On 2026-11-21 a coordinator's

    until ls $D | grep X | grep -vq OLD; do sleep 20; done

never ended and ran 2 h 50 min after its condition was true.

THE RULE. A `grep` CALL (code, not quoted text, a heredoc body or a comment) carrying both -v and
-q is refused, naming the two safe forms: `command grep -vq ...` (the system grep, past the
function) and `grep -v ... | read` (no -q: -v alone exits right). `/usr/bin/grep` and
`command grep` are not the function and pass.

THE LIFT. `goh shell-check` (SessionStart) runs the repro against the harness binary
(`CLAUDE_CODE_EXECPATH`) and records the exit code in `STATE` with the binary it probed. While that
record says 0 for the binary in use now, the bug is gone and the refusal is lifted; a new binary
(a Claude Code update) is unprobed and refused until the next shell-check. The escape is
`# grep-vq-ok: <reason>`.

The tool shell's `grep` is Claude Code's ugrep, whose `-v -q` exits 1 while a non-matching line
exists (GOH.UGREPVQ, 2026-11-21: an `until ... Invoked by grep -vq OLD` wait ran 2 h 50 min). The
Bash guard refuses a `grep` call with both -v and -q; `command grep` and `/usr/bin/grep` pass; `goh
shell-check`'s record (`$XDG_STATE_HOME/goh/harness_grep.json`, `toolshell/grep_probe.rs`) of exit
0 for the binary in use lifts it. Escape `# grep-vq-ok: <reason>`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

import busyloop
import self_match

ESCAPE = re.compile(r"#\s*grep-vq-ok:\s*\S")
_GREP = re.compile(r"(?<![\w./$-])grep\b(?![\w.-])")
_COMMAND_BEFORE = re.compile(r"(?:^|[\s;&|(`])(?:command|builtin)\s+$")
_GREP_ARG = set("ABCmdDef")


def state_path(env: Mapping[str, str] | None = None) -> Path:
    """Where `goh shell-check` records the probe: `$XDG_STATE_HOME/goh/harness_grep.json`."""
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(env.get("HOME", ""), ".local", "state")
    return Path(base) / "goh" / "harness_grep.json"


def fixed(env: Mapping[str, str] | None = None, path: Path | None = None) -> bool:
    """True when shell-check probed THIS harness binary and its `grep -vq` exited 0."""
    env = os.environ if env is None else env
    try:
        rec = json.loads((path or state_path(env)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(rec, dict)
        and rec.get("vq_exit") == 0
        and bool(rec.get("execpath"))
        and rec.get("execpath") == env.get("CLAUDE_CODE_EXECPATH")
    )


def _inverts_quietly(argv: list[str]) -> bool:
    longs = {a.split("=", 1)[0] for a in argv if a.startswith("--")}
    flags, _ = self_match._flags([a for a in argv if not a.startswith("--")], _GREP_ARG)
    invert = "v" in flags or "--invert-match" in longs
    quiet = "q" in flags or bool(longs & {"--quiet", "--silent"})
    return invert and quiet


def calls(command: str) -> list[str]:
    """The text of each harness-`grep` call in COMMAND that has both -v and -q."""
    masked = busyloop.mask(command)
    found = []
    for match in _GREP.finditer(masked):
        if _COMMAND_BEFORE.search(masked[: match.start()]):
            continue  # `command grep`: the system grep, past the function
        stop = self_match._end_of_command(command, match.end())
        argv = self_match._split(command[match.end() : stop])
        if argv is not None and _inverts_quietly(argv):
            found.append(command[match.start() : stop].strip())
    return found


def judge(
    command: str, env: Mapping[str, str] | None = None, path: Path | None = None
) -> str | None:
    """The refusal for a harness `grep -v -q`, or None. See the module docstring."""
    if ESCAPE.search(command):
        return None
    found = calls(command)
    if not found or fixed(env, path):
        return None
    return (
        f"`{found[0]}` runs the tool shell's grep, a function over Claude Code's embedded ugrep, "
        "which exits 1 for -v -q whenever a non-matching line EXISTS (POSIX grep exits 0): "
        "`printf 'a\\nb\\n' | grep -vq a` gives 1. A loop on it never ends (2 h 50 min on "
        "2026-11-21). Run instead: `command grep -vq ...` (the system grep), or drop -q and "
        "test for output (`grep -v ... | read`). `goh shell-check` lifts this refusal once the "
        "harness grep answers 0. To keep it on purpose, add `# grep-vq-ok: <reason>`."
    )
