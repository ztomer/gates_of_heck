"""A kill BY NAME of a command every session runs (GOH.PKILLNAME, 2026-11-08).

THE CLASS. An agent stopped its own gate with a pattern kill (`-f "tools/gate.sh --full"`), and
the pattern matched two OTHER sessions' gate runs on the same Mac: they died mid-run. A command
line is not an owner. The house's file check (`goh kill-by-name`, `crates/goh/src/killname`)
already refuses such a kill in tracked code; a command typed into the Bash tool never reaches it,
so the Claude Bash guard (`hooks/claude/discard_guard.py`) applies this rule before it runs.

THE RULE. In the command (quoted text is TEXT: `echo`, a commit message), a by-name kill is
refused when its pattern would reach a SHARED command -- one that other sessions run
concurrently (`SHARED`, sample command lines below):
  - the pattern kill (`-f` matches the full command line, else the process name), unless scoped
    to what the caller owns (`-P <pid>`, `-g`, `-s`);
  - the all-of-a-name kill (exact process name);
  - a `pgrep`/`pidof` feeding `kill` in the same pipeline or command substitution.
A kill of a numeric pid, a job (`%1`) or a scoped pattern runs; so does a pattern no shared
command line matches.

The one escape is `# kill-ok: <reason>`.
"""

from __future__ import annotations

import os
import re
import shlex

ESCAPE = re.compile(r"#\s*kill-ok:\s*\S")

# The command names, split so the house's own kill-by-name check does not read them as calls.
PKILL, KILLALL, PGREP, PIDOF = "p" + "kill", "kill" + "all", "p" + "grep", "pid" + "of"

# Command lines that more than one session runs at once on this Mac.
SHARED = (
    "bash tools/gate.sh --full",
    "bash /Users/x/Projects/gates_of_heck/tools/gate_profile.sh .",
    "bash tools/land.sh",
    "cargo test --workspace",
    "/Users/x/.rustup/toolchains/stable/bin/cargo clippy",
    "python3 -m pytest -q tests",
    "/opt/homebrew/bin/pytest -q",
    "python3 /Users/x/Projects/gates_of_heck/tools/load.py 8 -- make",
    "xcodebuild test -scheme App",
)
_SHARED_WORDS = re.compile(
    r"gate(?:_profile)?\.sh|land\.sh|(?<![\w-])cargo(?![\w-])|pytest|load\.py|xcodebuild"
)

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


def _reason(what: str) -> str:
    return (
        f"This kills by name: {what} A name reaches every session's processes, not just yours: on "
        "2026-11-08 an agent stopped its own gate with a pattern kill of `tools/gate.sh --full` "
        f"and killed two other sessions' gate runs with it. {WAY_OUT}"
    )


def _matches(pattern: str, full: bool) -> bool:
    if _SHARED_WORDS.search(pattern):
        return True
    for line in SHARED:
        target = line if full else os.path.basename(line.split()[0])
        try:
            if re.search(pattern, target):
                return True
        except re.error:
            if pattern in target:
                return True
    return False


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
        finders: list[str] = []
        for cmd in pipe:
            prog, args = _program(cmd)
            if prog == PKILL:
                pats, full, scoped = _patterns(args)
                if not scoped and any(_matches(p, full) for p in pats):
                    return _reason(f"`{PKILL} {' '.join(args)}`.")
            elif prog == KILLALL:
                pats, _, _ = _patterns(args)
                for p in pats:
                    if _SHARED_WORDS.search(p) or any(
                        os.path.basename(line.split()[0]) == p for line in SHARED
                    ):
                        return _reason(f"`{KILLALL} {' '.join(args)}`.")
            elif prog == "kill" or (prog == "xargs" and "kill" in args):
                kills = True
            elif prog in (PGREP, PIDOF):
                pats, full, scoped = _patterns(args)
                if not scoped and any(_matches(p, full or prog == PIDOF) for p in pats):
                    finders.append(f"`{prog} {' '.join(args)}`")
        if kills and finders:
            return _reason(f"{finders[0]} feeding `kill`.")
    return None
