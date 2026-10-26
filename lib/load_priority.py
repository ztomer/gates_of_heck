"""Is a command run at BACKGROUND priority? -- the house's ONE answer, for `tools/load.py` and the
Bash guard that reads a `load.py` command line before it runs.

THE CLASS (G1f, 2026-10-25). `load.py 32 -- taskpolicy -b loop.sh` ran 10h14m at load average ~154,
and `load.py 16 -- taskpolicy -b python3 -m pytest` was killed by its 20-minute deadline the same
day. Neither was a slow test. A command clamped to background priority (or a high nice, or
SCHED_IDLE) under N spinners at normal priority is STARVED BY CONSTRUCTION: the scheduler runs it
only when a core is idle, and the spinners exist so that none is. The experiment cannot measure
anything; it can only hang until the deadline ends it, and the spinners burn the whole time.

So a load experiment refuses a wrapped command whose argv lowers its own priority, names why, and
names the escape (`--background-ok <reason>`): a test that MEANS to measure starvation says so.

WHAT COUNTS. The wrappers that lower a command's CPU priority:

  * `taskpolicy -b`, `taskpolicy -c background|utility|maintenance` (macOS QoS clamps);
  * `nice` with an increment of NICE_FLOOR or more (`nice CMD` alone adds 10), in every spelling:
    `-n 19`, `-n19`, `-19` (the historical form), `--adjustment=19`;
  * `renice` to NICE_FLOOR or more (`renice -n 19 ...`, `renice 19 ...`);
  * `chrt -i` / `chrt --idle` (Linux SCHED_IDLE).

They are found where they would act: at the head of the argv, after the prefixes that run their
operand (`env [-i] [VAR=val...]`, `sudo [-u user...]`, `nohup`, `time`, `command`, `exec`,
`timeout DUR`, `caffeinate [-dimsu]`), after another priority wrapper, and inside a shell's `-c`
script (`sh -c 'taskpolicy -b x'`), whose simple commands are each judged.
"""

from __future__ import annotations

import re
import shlex

NICE_FLOOR = 10  # an increment this high is a background job (`nice CMD` alone adds 10)
QOS_CLAMPS = {"background", "utility", "maintenance"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
SEPARATORS = set(";&|")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
ESCAPE_FLAG = "--background-ok"
HELP = "allow a command at background priority, which N spinners starve (a reason is required)"


def _base(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _skip_env(argv: list[str]) -> list[str]:
    rest = argv[1:]
    while rest:
        tok = rest[0]
        if tok in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"):
            rest = rest[2:]
        elif tok.startswith("-") or ASSIGNMENT.match(tok):
            rest = rest[1:]
        else:
            break
    return rest


def _skip_sudo(argv: list[str]) -> list[str]:
    rest = argv[1:]
    takes_value = {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"}
    while rest:
        tok = rest[0]
        if tok == "--":
            return rest[1:]
        if tok in takes_value:
            rest = rest[2:]
        elif tok.startswith("-") or ASSIGNMENT.match(tok):
            rest = rest[1:]
        else:
            break
    return rest


def _skip_flags(argv: list[str]) -> list[str]:
    rest = argv[1:]
    while rest and rest[0].startswith("-"):
        if rest[0] == "--":
            return rest[1:]
        rest = rest[1:]
    return rest


def _skip_timeout(argv: list[str]) -> list[str]:
    rest = argv[1:]
    while rest and rest[0].startswith("-"):
        rest = rest[2:] if rest[0] in ("-s", "-k", "--signal", "--kill-after") else rest[1:]
    return rest[1:]  # the duration


def _int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def _nice(argv: list[str]) -> tuple[str | None, list[str]]:
    """The finding for a `nice` argv, and the operand command."""
    rest = argv[1:]
    increment = 10
    if rest and rest[0] == "-n" and len(rest) > 1:
        increment, rest = _int(rest[1]) or 0, rest[2:]
    elif rest and rest[0].startswith("--adjustment="):
        increment, rest = _int(rest[0].split("=", 1)[1]) or 0, rest[1:]
    elif rest and rest[0].startswith("-n"):
        increment, rest = _int(rest[0][2:]) or 0, rest[1:]
    elif rest and re.fullmatch(r"-\d+", rest[0]):
        increment, rest = int(rest[0][1:]), rest[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if increment >= NICE_FLOOR:
        return f"`nice` (+{increment})", rest
    return None, rest


def _renice(argv: list[str]) -> str | None:
    rest = argv[1:]
    if rest and rest[0] == "-n" and len(rest) > 1:
        value = _int(rest[1])
    else:
        value = _int(rest[0]) if rest else None
    if value is not None and value >= NICE_FLOOR:
        return f"`renice` ({value})"
    return None


def _taskpolicy(argv: list[str]) -> tuple[str | None, list[str]]:
    rest = argv[1:]
    found = None
    while rest and rest[0].startswith("-"):
        tok = rest[0]
        if tok == "-b":
            found = "`taskpolicy -b`"
            rest = rest[1:]
        elif tok in ("-c", "-d", "-g", "-t", "-l", "-p") and len(rest) > 1:
            if tok == "-c" and rest[1] in QOS_CLAMPS:
                found = f"`taskpolicy -c {rest[1]}`"
            rest = rest[2:]
        else:
            rest = rest[1:]
    return found, rest


def _chrt(argv: list[str]) -> tuple[str | None, list[str]]:
    rest = argv[1:]
    found = None
    while rest and rest[0].startswith("-"):
        if rest[0] in ("-i", "--idle"):
            found = "`chrt --idle` (SCHED_IDLE)"
        rest = rest[1:]
    return found, rest[1:]  # the priority


def segments(script: str) -> list[list[str]]:
    """A shell script's simple commands, split on shell separators."""
    lex = shlex.shlex(script, posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = "#"
    segs: list[list[str]] = [[]]
    try:
        for tok in lex:
            if set(tok) <= SEPARATORS:
                segs.append([])
            else:
                segs[-1].append(tok)
    except ValueError:  # an unbalanced quote: judge what was read
        pass
    return [seg for seg in segs if seg]


def background_wrapper(argv: list[str], depth: int = 0) -> str | None:
    """The wrapper that puts `argv` at background priority (e.g. "`taskpolicy -b`"), or None."""
    while argv and depth < 16:
        depth += 1
        name = _base(argv[0])
        if ASSIGNMENT.match(argv[0]):
            argv = argv[1:]
        elif name == "env":
            argv = _skip_env(argv)
        elif name in ("sudo", "doas"):
            argv = _skip_sudo(argv)
        elif name in ("nohup", "time", "command", "exec", "caffeinate", "stdbuf", "xcrun"):
            argv = _skip_flags(argv)
        elif name in ("timeout", "gtimeout"):
            argv = _skip_timeout(argv)
        elif name == "taskpolicy":
            found, argv = _taskpolicy(argv)
            if found:
                return found
        elif name == "nice":
            found, argv = _nice(argv)
            if found:
                return found
        elif name == "renice":
            return _renice(argv)
        elif name == "chrt":
            found, argv = _chrt(argv)
            if found:
                return found
        elif name in SHELLS:
            script = _shell_script(argv)
            if script is None:
                return None
            for seg in segments(script):
                found = background_wrapper(seg, depth)
                if found:
                    return found
            return None
        else:
            return None
    return None


def _shell_script(argv: list[str]) -> str | None:
    rest = argv[1:]
    while rest and rest[0].startswith("-"):
        if "c" in rest[0][1:] and not rest[0].startswith("--"):
            return rest[1] if len(rest) > 1 else None
        rest = rest[1:]
    return None


def refusal(count: int, wrapper: str) -> str:
    """Why a load experiment over a background-priority command is refused, and the way out."""
    return (
        f"the command runs at background priority ({wrapper}): under {count} spinner(s) at normal "
        "priority it is starved by construction -- the scheduler gives it a core only when one is "
        "idle, and the spinners exist so that none is. That is how `load.py 32 -- taskpolicy -b "
        "loop.sh` ran 10h14m and `load.py 16 -- taskpolicy -b python3 -m pytest` was killed by its "
        "deadline (2026-10-25). Drop the priority wrapper and run the command at normal priority; "
        f"to measure starvation on purpose, pass `{ESCAPE_FLAG} <reason>`."
    )


def preflight(count: int, command: list[str], reason: str | None) -> str | None:
    """`tools/load.py`'s refusal for an experiment over `command`, or None when it may run."""
    if reason is not None and not reason.strip():
        return (
            f"{ESCAPE_FLAG} with no reason excuses nothing: name why the command is meant to run "
            "starved"
        )
    wrapper = background_wrapper(command)
    if count > 0 and wrapper and reason is None:
        return refusal(count, wrapper)
    return None


def judge_invocation(tokens: list[str]) -> str | None:
    """The refusal for a `load.py` argv (what follows the script), or None.

    Read the way the tool reads it: options, then N, then `--`, then the command. A command line
    the guard cannot read is the tool's to refuse, not the guard's.
    """
    rest = list(tokens)
    escaped = False
    while rest and rest[0].startswith("--") and rest[0] != "--":
        flag = rest[0]
        if flag == ESCAPE_FLAG:
            escaped = len(rest) > 1 and bool(rest[1].strip())
            rest = rest[2:]
        elif flag.startswith(ESCAPE_FLAG + "="):
            escaped = bool(flag.split("=", 1)[1].strip())
            rest = rest[1:]
        elif flag in ("--dir", "--max"):
            rest = rest[2:]
        else:
            rest = rest[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        return None
    count = _int(rest[0])
    command = rest[1:]
    if command and command[0] == "--":
        command = command[1:]
    if escaped or not count or count <= 0 or not command:
        return None
    wrapper = background_wrapper(command)
    return refusal(count, wrapper) if wrapper else None
