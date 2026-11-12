"""A kill BY NAME of a command every session runs (GOH.PKILLNAME, 2026-11-08).

THE CLASS. An agent stopped its own gate with a pattern kill (`-f "tools/gate.sh --full"`), and
the pattern matched two OTHER sessions' gate runs on the same Mac: they died mid-run. A command
line is not an owner. The house's file check (`goh kill-by-name`, `crates/goh/src/killname`)
already refuses such a kill in tracked code; a command typed into the Bash tool never reaches it,
so the Claude Bash guard (`hooks/claude/discard_guard.py`) applies this rule before it runs.

THE RULE. In the command (quoted text is TEXT: `echo`, a commit message), a by-name kill is
refused (until GOH.NOPKILL, only when its pattern reached a command other sessions run; now
whatever it names, below):
  - the pattern kill (`-f` matches the full command line, else the process name), unless scoped
    to what the caller owns (`-P <pid>`, `-g`, `-s`);
  - the all-of-a-name kill (exact process name);
  - a `pgrep`/`pidof` feeding `kill` in the same pipeline or command substitution.
A kill of a numeric pid, a job (`%1`) or a scoped pattern runs.

The one escape is `# kill-ok: <reason>`.

EVERY NAME, NOT ONLY A SHARED ONE (GOH.NOPKILL, 2026-11-09). The loop coordinator stopped its own
watcher with a pattern kill of the RESULT FILE's name (`20261109T104119Z-4439f143.json`), which is
no shared command -- and the `goh ship` it was watching carried the same path on its command line,
so the ship died at push (exit 143). No list of shared commands can know what else carries a
pattern. So an unscoped pattern kill (`-f` or a bare pattern), an all-of-a-name kill, and a pgrep
feeding kill are refused whatever they name, and the refusal names what the pattern matches RIGHT
NOW (`pgrep -fl`), so the caller sees the process it would have hit. A scoped kill (`-P <pid>`,
`-g`, `-s`), a numeric pid and a job still run.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess

ESCAPE = re.compile(r"#\s*kill-ok:\s*\S")

# The command names, split so the house's own kill-by-name check does not read them as calls.
PKILL, KILLALL, PGREP, PIDOF = "p" + "kill", "kill" + "all", "p" + "grep", "pid" + "of"

_WRAPPERS = {"sudo", "doas", "env", "nohup", "exec", "command", "nice", "time", "rtk"}
_SCOPED = {"-P", "-g", "-s", "--parent", "--pgroup", "--session"}
_VALUED = {"-u", "-U", "-G", "-t", "-F", "--signal", "-P", "-g", "-s", "--parent", "--pgroup",
           "--session"}  # fmt: skip
_PIPELINE_BREAK = {";", "&&", "||", "&", "\n"}

WAY_OUT = (
    "Kill what YOU started, by its pid: background it (`cmd & pid=$!`, or the Bash tool's "
    "run_in_background) and `kill $pid`; a tree you own is `" + PKILL + " -P <your pid>`. Load is "
    'made through `python3 "$GOH_DIR/tools/load.py" N -- <cmd>`, which reaps exactly its own '
    "children. Never stop another session's run. To kill by name on purpose (a machine nobody "
    "else uses), add `# kill-ok: <reason>` to the command."
)


def live(pattern: str, full: bool, exact: bool = False) -> list[str]:
    """`pid command` of each process the pattern matches now (pgrep's own answer), at most 8."""
    argv = [PGREP, "-l"] + (["-f"] if full else []) + (["-x"] if exact else []) + ["--", pattern]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False,
                              stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    mine = {os.getpid(), os.getppid()}
    rows = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    return [r for r in rows if not r.split()[0].isdigit() or int(r.split()[0]) not in mine][:8]


def _hits(pairs: list[tuple[str, bool, bool]]) -> str:
    rows = [row for pattern, full, exact in pairs for row in live(pattern, full, exact)]
    if not rows:
        return "It matches no process right now, so a later one it would hit is unknown. "
    return "Right now it would hit: " + "; ".join(r[:120] for r in rows[:8]) + ". "


def _reason(what: str, pairs: list[tuple[str, bool, bool]] | None = None) -> str:
    return (
        f"This kills by name: {what} {_hits(pairs or [])}A name reaches every process whose name "
        "or command line carries it, not just yours: on 2026-11-08 an agent stopped its own gate "
        "with a pattern kill of `tools/gate.sh --full` and killed two other sessions' gate runs, "
        "and on 2026-11-09 a pattern kill of a result file's name killed the `goh ship` whose "
        f"command line carried that path. Stop a process by the PID you hold (`kill <pid>`; a "
        f"background task by TaskStop <id>). {WAY_OUT}"
    )


def _tokens(command: str) -> list[str]:
    lex = shlex.shlex(command.replace("$(", " $( ").replace("`", " ` "), posix=True,
                      punctuation_chars=";&|()")  # fmt: skip
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def _pipelines(tokens: list[str]) -> list[list[list[str]]]:
    """Pipelines of simple commands; a `$(`/`(`/`)` starts a new simple command in the same one."""
    pipes: list[list[list[str]]] = [[[]]]
    for tok in tokens:
        if tok in _PIPELINE_BREAK or (set(tok) <= set(";&") and tok):
            pipes.append([[]])
        elif tok in {"|", "$(", "(", ")", "`", "|&"} or (set(tok) <= set("|()") and tok):
            pipes[-1].append([])
        else:
            pipes[-1][-1].append(tok)
    return [[c for c in p if c] for p in pipes if any(p)]


def _program(cmd: list[str]) -> tuple[str, list[str]]:
    """The program a simple command runs, past wrappers (`sudo`, `timeout 5`, `VAR=x`), and its args."""
    i = 0
    while i < len(cmd):
        word = os.path.basename(cmd[i])
        if word == "timeout":
            i += 2
        elif word in _WRAPPERS or cmd[i].startswith("-") and i > 0 or re.match(r"^\w+=", cmd[i]):
            i += 1
        else:
            return word, cmd[i + 1 :]
    return "", []


def _patterns(args: list[str]) -> tuple[list[str], bool, bool]:
    """(patterns, -f given, scoped to the caller)."""
    pats: list[str] = []
    full = scoped = False
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg in _SCOPED or any(arg.startswith(s + "=") for s in _SCOPED if s.startswith("--")):
            scoped = True
        if arg.startswith("-") and len(arg) > 1:
            if arg in ("-f", "--full") or (not arg.startswith("--") and "f" in arg[1:]
                                           and arg[1:].isalpha()):  # fmt: skip
                full = True
            if arg in _VALUED:
                skip = True
            continue
        pats.append(arg)
    return pats, full, scoped


def judge(command: str) -> str | None:
    """The refusal for a by-name kill of a shared command, or None when the command runs."""
    if ESCAPE.search(command):
        return None
    try:
        pipes = _pipelines(_tokens(command))
    except ValueError:
        return None
    for pipe in pipes:
        kills = False
        finders: list[tuple[str, list[tuple[str, bool, bool]]]] = []
        for cmd in pipe:
            prog, args = _program(cmd)
            if prog == PKILL:
                pats, full, scoped = _patterns(args)
                if not scoped and pats:
                    return _reason(f"`{PKILL} {' '.join(args)}`.", [(p, full, False) for p in pats])
            elif prog == KILLALL:
                pats, _, _ = _patterns(args)
                if pats:
                    return _reason(
                        f"`{KILLALL} {' '.join(args)}`.", [(p, False, True) for p in pats]
                    )
            elif prog == "kill" or (prog == "xargs" and "kill" in args):
                kills = True
            elif prog in (PGREP, PIDOF):
                pats, full, scoped = _patterns(args)
                if not scoped and pats:
                    exact = prog == PIDOF
                    finders.append((f"`{prog} {' '.join(args)}`",
                                    [(p, full, exact) for p in pats]))  # fmt: skip
        if kills and finders:
            return _reason(f"{finders[0][0]} feeding `kill`.", finders[0][1])
    return None
