"""A wait loop whose condition matches ITS OWN shell, and so can never end (2026-11-01).

THE CLASS. Three agent background shells ran

    until ! pgrep -f "git push origin main"; do sleep 30; done

for 8-9 hours. They could not end: `pgrep -f` matches against each process's FULL command line, and
the waiting shell's own command line (`zsh -c '... until ! pgrep -f "git push origin main" ...'`)
contains the pattern. `pgrep` skips itself, never its parent. The same goes for `while pgrep -f X`,
`pkill -0 -f X`, and `ps ... | grep X` (whose `grep X` process matches X as well -- the reason the
bracket trick exists). The invariant violated: a wait on "no process matching P" must not itself be
a process matching P.

THE RULE. In the CODE of a command (quoted spans, heredoc bodies and comments are TEXT, masked out
by `busyloop.mask`), a `while`/`until` whose CONDITION runs

  * `pgrep -f PATTERN` or `pkill -0 -f PATTERN`, or
  * `ps ... | grep PATTERN` (also egrep, fgrep, rg)

is refused when PATTERN, read as the regular expression it is, matches the command's own text --
which is what the waiting shell's command line holds. Judged by that match, not by "the pattern
appears literally", so the bracket trick (`"[g]it push"`: the regex matches `git push`, the text
holds `[g]it push`) passes by itself, and a bracketed pattern that is ALSO spelled plainly
elsewhere in the command (`git push ...; until ! pgrep -f "[g]it push"`) is still refused, as it
should be. A `grep -v` filter whose pattern matches the command (`| grep -v grep`) drops the
shell's own line, so the classic idiom is accepted.

A shell run as `sh|bash|zsh -c '<script>'` carries its script as a quoted string, so the string is
judged as a command in its own right (against the whole outer command text, which holds it).

Ways out, named in the refusal: wait on the PID (`while kill -0 $pid`), the bracket trick, or
`wait` for a job the shell started itself. The one escape is `# pgrep-ok: <reason>`.

EVERY OTHER PLACE THE CALL CAN STAND (GOH.PGREPSELF, 2026-11-16). The same invariant holds for a
`pgrep -f` / `pkill -f` that is no loop condition: `pgrep -f foo || foo &`, `if pgrep -f X`, a
`$(pgrep -f X | wc -l)`, a `pkill -f X` after the command that started X. Two lane loops, a
fleet-deploy over ssh and a camoufox neighbour kill met it (2026-11-15). `judge_anywhere` refuses

  * a `pgrep -f`/`pkill -f` at the top level whose pattern, read as a regex, matches the command's
    text at TWO places or more (its own spelling is one; the second is "elsewhere", so the shell
    that runs the command matches too -- one spelling alone is left to run, as before);
  * one inside a `sh|bash|zsh -c '<script>'` string or an `ssh host '<script>'` remote command whose
    pattern matches that string: the shell started for it holds the script in its command line, so
    one spelling is enough.

It passes when the command excludes its own pid (`$$`, `$BASHPID`, `$PPID`), and the bracket form
passes by itself. The refusal names the safe forms: a pid file or `$!`, waiting on a log line, the
bracket form. Same escape.

THE PAVED PATH FIRST (GOH.WAITSTOP, 2026-11-21). Every refusal opens with `Run instead:` and the
one `goh wait` / `goh stop` line that replaces the refused call (`lib/paved_path.py`): 114 of these
refusals in a week were rewritten by hand, often into the next refused shape.

Not judged: a file. A script's command line is `bash script.sh`, which does not contain the
script's patterns, so the loop is not a self-match there.

A wait loop whose condition matches ITS OWN shell, and so never ends (2026-11-01: three background
shells ran `until ! pgrep -f "git push origin main"; do sleep 30; done` for 8-9 hours -- `pgrep -f`
skips itself, never the parent whose command line holds the pattern). The Bash guard refuses a
`while`/`until` whose condition is `pgrep -f`, `pkill -0 -f` or `ps | grep` on a pattern that
regex-matches the command's own text (so the bracket trick passes by itself), reading quoted spans,
heredoc bodies and comments as TEXT (`busyloop.mask`) and a `sh -c '<script>'` string as a command;
a `grep -v` that drops the shell's line is accepted. Ways out named in the refusal: wait on the
PID, the bracket trick, `wait`. The escape is `# pgrep-ok: <reason>`.
"""

from __future__ import annotations

import os
import re
import shlex

import busyloop
import paved_path

ESCAPE = re.compile(r"#\s*pgrep-ok:\s*\S")

# `until`/`while` up to the `do` that ends its condition (the condition holds no do/done keyword).
_LOOP = re.compile(
    r"\b(?:until|while)\b(?P<cond>(?:(?!\bdo\b|\bdone\b)[\s\S])*?)(?:;|\n)[ \t]*do\b"
)
_PROG = re.compile(r"(?<![\w./-])(?P<prog>pgrep|pkill|e?grep|fgrep|rg)\b")
_PS = re.compile(r"(?<![\w./-])ps\b")
_TERMINATORS = set("|;&)`<>\n")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# pgrep/pkill options that take an argument (macOS and procps).
_PGREP_ARG = set("uUgGPstFdMN")
_GREP_ARG = set("ABCmdD")  # -e/-f are handled by hand: they carry the pattern / a file

WAY_OUT = (
    'Wait on the PID instead (`while kill -0 "$pid" 2>/dev/null; do sleep 30; done`), use the '
    'bracket trick so the pattern cannot match its own text (`pgrep -f "[g]it push"`), or '
    "`wait` for a job the shell itself started. To keep the loop on purpose, add "
    "`# pgrep-ok: <reason>` to the command."
)


def _instead(prog: str, pattern: str) -> str:
    """The paved-path line that replaces the call (GOH.WAITSTOP): a kill is `goh stop`, a look or
    a wait `goh wait`."""
    if prog != "pgrep":  # the kill of the pair
        return paved_path.stop_command(paved_path.live_pids(pattern))
    return paved_path.wait_command(pattern)


def _reason_anywhere(prog: str, pattern: str) -> str:
    bracket = f"[{pattern[0]}]{pattern[1:]}" if pattern[:1].isalnum() else "[f]oo"
    return (
        f"Run instead: {_instead(prog, pattern)}. "
        f"This `{prog} -f {pattern}` matches the shell that runs it: the pattern appears in that "
        "shell's OWN command line (the whole command, or the `sh -c` string / ssh remote command "
        f'that holds it), and `{prog}` skips itself, never its parent -- so "is it running" is '
        "always yes, a wait on it never ends, and a kill can take the shell down. Use the pid of "
        'what YOU started (`cmd & pid=$!`, or a pid file: `kill -0 "$(cat x.pid)"`), wait on a '
        "log line (`tail -f x.log | grep -m1 ready`), or the bracket form "
        f"(`{prog} -f '{bracket}'`; the regex cannot match its own spelling, e.g. `[f]oo` for "
        "`foo`). To keep it on purpose, add `# pgrep-ok: <reason>` to the command."
    )


def _reason(prog: str, pattern: str) -> str:
    return (
        f"Run instead: {paved_path.wait_command(pattern)}. "
        f"This wait can never end: its condition runs `{prog}` with the pattern `{pattern}`, "
        "and the pattern appears in the waiting shell's OWN command line (`zsh -c '... "
        f"{pattern} ...'`), which `{prog}` matches -- it skips itself, never its parent. Three "
        'background shells ran `until ! pgrep -f "git push origin main"; do sleep 30; done` for '
        f"8-9 hours that way (2026-11-01). {WAY_OUT}"
    )


def _split(text: str) -> list[str] | None:
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError:
        return None


def _flags(argv: list[str], takes_arg: set[str]) -> tuple[set[str], list[str]]:
    """(the single-letter flags, the positionals) of a command's arguments."""
    flags: set[str] = set()
    rest: list[str] = []
    skip = False
    for index, tok in enumerate(argv):
        if skip:
            skip = False
            continue
        if tok == "--full":
            flags.add("f")
        elif tok == "--ignore-case":
            flags.add("i")
        elif tok.startswith("--") or tok == "-":
            continue
        elif tok.startswith("-") and len(tok) > 1:
            for pos, char in enumerate(tok[1:], start=1):
                flags.add(char)
                if char in takes_arg:
                    skip = pos == len(tok) - 1 and index + 1 < len(argv)
                    break
        else:
            rest.append(tok)
    return flags, rest


def _self_matches(pattern: str, command: str, ignore_case: bool, literal: bool = False) -> bool:
    if not pattern:
        return False
    try:
        rx = re.compile(
            re.escape(pattern) if literal else pattern, re.IGNORECASE if ignore_case else 0
        )
    except re.error:
        return pattern in command
    return rx.search(command) is not None


def _end_of_command(text: str, start: int) -> int:
    """Where the simple command that starts at `start` ends: the first unquoted terminator."""
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char in _TERMINATORS:
            break
        index += 1
    if index < len(text) and text[index] in "<>":  # `2>/dev/null`: the fd is the redirection's
        fd = index
        while fd > start and text[fd - 1].isdigit():
            fd -= 1
        if fd < index and (fd == start or text[fd - 1] in " \t"):
            index = fd
    return index


def _calls(original: str, start: int, end: int) -> list[tuple[str, list[str]]]:
    """The (program, argv) of each pgrep/pkill/grep-family simple command in [start, end).

    The condition is read as code except inside single quotes; a command substitution in double
    quotes (`[ -z "$(pgrep -f X)" ]`) is code, so double quotes are walked, not blanked.
    """
    cond = original[start:end]
    live = list(cond)
    quote = ""
    for index, char in enumerate(cond):
        if quote == "'":
            live[index] = " "
            if char == "'":
                quote = ""
        elif char == "'" and quote != '"':
            quote = "'"
            live[index] = " "
        elif char == '"':
            quote = "" if quote == '"' else '"'
    code = "".join(live)
    found = []
    for match in _PROG.finditer(code):
        stop = _end_of_command(cond, match.end())
        argv = _split(cond[match.end() : stop])
        if argv is not None:
            found.append((match.group("prog"), argv))
    return found


def _grep_patterns(argv: list[str]) -> tuple[bool, bool, list[str], bool]:
    """(invert, ignore_case, patterns, fixed_string) of a grep-family command's arguments."""
    flags, rest = _flags([a for a in argv if a not in ("-e",)], _GREP_ARG)
    patterns = rest[:1]
    for index, tok in enumerate(argv):
        if tok == "-e" and index + 1 < len(argv):
            patterns = [argv[index + 1]]
    return "v" in flags, "i" in flags, patterns, "F" in flags


def _judge_loop(original: str, start: int, end: int, context: str) -> str | None:
    calls = _calls(original, start, end)
    # A `grep -v` whose pattern matches the command drops the waiting shell's own line.
    for prog, argv in calls:
        if prog in ("grep", "egrep", "fgrep", "rg"):
            invert, icase, pats, fixed = _grep_patterns(argv)
            if invert and any(_self_matches(p, context, icase, fixed) for p in pats):
                return None
    for prog, argv in calls:
        if prog in ("pgrep", "pkill"):
            flags, pos = _flags(argv, _PGREP_ARG)
            if "f" not in flags or "x" in flags or not pos:
                continue
            if prog == "pkill" and "0" not in flags:
                continue
            if _self_matches(pos[-1], context, "i" in flags):
                return _reason(prog, pos[-1])
        elif _PS.search(original, start, end):
            invert, icase, pats, fixed = _grep_patterns(argv)
            if not invert and pats and _self_matches(pats[0], context, icase, fixed):
                return _reason(prog, pats[0])
    return None


_OWN_PID = re.compile(r"\$\$|\$\{\$\}|\$\{?BASHPID\}?|\$\{?PPID\}?")
_SSH_ARG = set("bcDEeFIiJLlmOopQRSWw")  # ssh options that take an argument
_OPEN_SUBST = re.compile(r"\$\([^)]*$")  # inside an unclosed `$(`


def _in_substitution(prefix: str) -> bool:
    """Whether the line up to a call is inside an unclosed `$(` or an OPEN backtick (an odd count:
    the second of a closed pair followed by text is not one, GOH.PGREPREDIRECT)."""
    return bool(_OPEN_SUBST.search(prefix)) or prefix.count("`") % 2 == 1


def _nested(command: str) -> list[str]:
    """The scripts the command hands to a shell of its own: `sh|bash|zsh -c '<script>'` and the
    remote command of `ssh [opts] host <script>` (which sshd runs as `sh -c "<script>"`)."""
    lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=";&|")
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        toks = list(lex)
    except ValueError:
        return []
    segs: list[list[str]] = [[]]
    for tok in toks:
        if set(tok) <= set(";&|"):
            segs.append([])
        else:
            segs[-1].append(tok)
    found = []
    for seg in segs:
        for index, tok in enumerate(seg):
            name = os.path.basename(tok)
            if name in _SHELLS:
                for at in range(index + 1, min(index + 4, len(seg) - 1)):
                    if seg[at].startswith("-") and "c" in seg[at][1:]:
                        found.append(seg[at + 1])
                        break
            elif name == "ssh":
                at = index + 1
                while at < len(seg) and seg[at].startswith("-"):
                    at += 2 if len(seg[at]) == 2 and seg[at][1] in _SSH_ARG else 1
                if at + 1 < len(seg):  # seg[at] is the host; the rest is the remote command
                    found.append(" ".join(seg[at + 1 :]))
            else:
                continue
            break
    return found


def _selfmatching(script: str, context: str, minimum: int) -> str | None:
    """The refusal for a `pgrep -f` / `pkill -f` that is CODE in `script` and whose pattern matches
    `context` at `minimum` places or more (the call's own spelling is one of them), or None."""
    masked = busyloop.mask(script)
    unexpanded = busyloop.blank_quoted_heredocs(script)
    for match in _PROG.finditer(script):
        prefix = script[: match.start()].rsplit("\n", 1)[-1]
        if masked[match.start()] != script[match.start()] and not _in_substitution(prefix):
            continue  # quoted text, a heredoc body or a comment: not a call
        if unexpanded[match.start()] != script[match.start()]:
            continue  # a quoted heredoc's body, where even a backtick is text
        prog = match.group("prog")
        if not prog.startswith("p"):  # pgrep / pkill (the grep family is _PROG's other half)
            continue
        argv = _split(script[match.end() : _end_of_command(script, match.end())])
        if argv is None:
            continue
        flags, pos = _flags(argv, _PGREP_ARG)
        if "f" not in flags or "x" in flags or not pos or not pos[-1]:
            continue
        pattern = pos[-1]
        try:
            rx = re.compile(pattern, re.IGNORECASE if "i" in flags else 0)
        except re.error:
            rx = re.compile(re.escape(pattern))
        if len(rx.findall(context)) >= minimum:
            return _reason_anywhere(prog, pattern)
    return None


def judge_anywhere(command: str) -> str | None:
    """The refusal for a `pgrep -f` / `pkill -f` that matches the shell running it, wherever the
    call stands (GOH.PGREPSELF), or None. `# pgrep-ok: <reason>`; see the module docstring."""
    if ESCAPE.search(command):
        return None
    # The top level: the call's own spelling is one match; a second is "elsewhere".
    reason = None if _OWN_PID.search(command) else _selfmatching(command, command, 2)
    if reason:
        return reason
    pending, seen = _nested(command), set()
    while pending:  # a script handed to a shell: that shell's command line IS the script
        script = pending.pop()
        if script in seen:
            continue
        seen.add(script)
        reason = None if _OWN_PID.search(script) else _selfmatching(script, script, 1)
        if reason:
            return reason
        pending += _nested(script)
    return None


def judge(command: str, context: str | None = None) -> str | None:
    """The refusal for a command whose wait loop matches its own shell, or None.

    `context` is the text the waiting shell's command line holds: the whole command, which a
    nested `bash -c '<script>'` is judged against too.
    """
    if ESCAPE.search(command):
        return None
    top = context is None
    context = command if context is None else context
    masked = busyloop.mask(command)
    for match in _LOOP.finditer(masked):
        reason = _judge_loop(command, match.start("cond"), match.end("cond"), context)
        if reason:
            return reason
    # A `sh -c '<script>'` carries its script in a string: judge the string as a command.
    toks = _split(command.replace("\n", " ; ")) or []
    for index, tok in enumerate(toks[:-1]):
        if os.path.basename(tok) in _SHELLS:
            for arg_at in range(index + 1, min(index + 4, len(toks))):
                if toks[arg_at].startswith("-") and "c" in toks[arg_at][1:]:
                    inner = toks[arg_at + 1] if arg_at + 1 < len(toks) else ""
                    reason = judge(inner, context) if inner != command else None
                    if reason:
                        return reason
                    break
    return judge_anywhere(command) if top else None
