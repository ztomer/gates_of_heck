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
comments are not commands). A READER is a FILTER whose argv names no input file -- a program that
reads its stdin when given no file operand (`cat`, `grep`, `jq`, `sed`, `awk`, `sort`, `wc`, ...) --
or one whose operands are never input (`tee`'s are outputs, `tr`'s are sets, `xargs`'s a command);
the read builtins (`read`, `mapfile`; not `-t`, not `-u`); `python`/`python3` with `-` or no
script;
and a shell that runs its stdin as its script (`sh -s`, a bare `bash`). One rule judges every filter
(GOH.STDINFILTERS): its options' values are not operands, its leading script operand is not a file
unless an option supplied the script (`grep -e`, `jq -f`, `awk -f`), an operand `-` is stdin, and an
option that names the input or reads none (`grep -r`, `jq -n`, `sed -i`, `xargs -a`, an awk program
of BEGIN blocks only) makes it no reader. `FILTERS` holds each filter's argv grammar, not a verdict.
A reader is FED by a heredoc, a here-string, a `<` redirect or a pipe into that same command -- or
into the compound command around it (`cmd | while read`, `done < f`, `{ cat; } < f`) -- or by
running in the background (a background list reads /dev/null). A reader under `timeout` is bounded
and runs.
One fed or bounded reader beside an unfed one does not excuse it. The escape is
`# stdin-ok: <reason>`.

WHAT THE SPLITTER MUST NOT MISREAD (GOH.STDINGUARDFP, `shell_segments._protect`): a QUOTED or
backslashed `;` `&` `|` is a word, not a separator (`tr '\\n' ';' < $L` was cut at the `';'` and its
`< $L` judged a command of its own); a redirect glued to a word (`tr a b<$F`) is its own token; `>&`
`<&` `&>` `>|` are redirection operators; a `#` comment ends at its newline; a quoted
`<`/`>` is a `Literal` word, never a redirect. `<<WORD` is a heredoc (its body cut out and
marked), `<<<` a here-string, `<(cmd)` a process substitution that is a WORD (an operand,
not a feed): `<`, `<<<` and a heredoc feed the command, `<(` does not.

The detector for the readers already hung is `hooks/claude/stdin_hangs.py` (`goh agent-stalls`),
and it asks `reads_stdin` the same question of a live process's argv: one definition of a reader.
A read BUILTIN has no process of its own (the shell is the reader); `shell_reads` names it in the
text a blocked shell evals, for the detector to name the shell itself.

The discard guard's refusal of a stdin reader nothing feeds (a filter whose argv names no input
file, judged by ONE rule over each filter's argv grammar in `FILTERS`: `cat`, `grep`, `jq`, `sed`,
`awk`, `sort`, `wc`, `head`, `xargs`, `tee`, ...; the read builtins; `python3 -`; `sh -s`;
GOH.STDINFILTERS): the Bash tool feeds `< /dev/null` unless the command holds a heredoc, and then
fd 0 is the harness's socket, which never closes; `cat > /tmp/x.py` beside a `python3 - <<'EOF'`
hung 5h51m (GOH.STDINHANG). A heredoc, here-string, pipe or `<` on the command, or on the compound
around it, feeds it. `reads_stdin` is also the stall detector's definition of a reader. Escape:
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import shell_segments

ESCAPE = re.compile(r"#\s*stdin-ok:\s*\S")
REDIRECT = re.compile(r"^(\d*|&)(>>|>\||>&|>|<<<|<>|<&|<)(.*)$")
STDIN_OPS = {"<", "<>", "<&", "<<<"}
PYTHON = re.compile(r"^python(\d+(\.\d+)*)?$")
TIMEOUTS = {"timeout", "gtimeout"}
OPENERS = {"while": "done", "until": "done", "if": "fi", "for": "done", "select": "done", "{": "}"}
SKIP = {"do", "then", "else", "elif", "!", "time"}
CLOSERS = {"done", "fi", "}"}
BUILTIN_READERS = {"read", "mapfile", "readarray"}
INFO = frozenset({"--help", "--version"})


@dataclass(frozen=True)
class Filter:
    """The argv grammar of a program that reads its stdin when its argv names no input file.

    `valued`/`long_valued`: options whose value is the next word (a long one's count of words);
    `script`: leading operands that are the program, not input, unless one of `script_opts` gave it;
    `no_input`/`long_no_input`: options that name the input or read none; `operands`: "files", or
    "never" when no operand is ever input (tee's outputs, tr's sets, xargs's command)."""

    valued: str = ""
    long_valued: dict[str, int] = field(default_factory=dict)
    script: int = 0
    script_opts: str = ""
    long_script: frozenset[str] = frozenset()
    no_input: str = ""
    long_no_input: frozenset[str] = frozenset()
    operands: str = "files"
    rest_args: frozenset[str] = frozenset()  # after these, operands past the script are not files
    assignments: bool = False  # `var=val` operands are not files (awk)
    options_end: bool = False  # its options end at its first operand (xargs's command)


_GREP = Filter(
    valued="ABCDdefm",
    long_valued=dict.fromkeys(
        ("--regexp", "--file", "--max-count", "--after-context", "--before-context", "--context",
         "--label", "--include", "--exclude", "--exclude-dir", "--devices", "--directories"), 1),
    script=1, script_opts="ef", long_script=frozenset({"--regexp", "--file"}),
    no_input="rRV", long_no_input=frozenset({"--recursive", "--dereference-recursive"}),
)  # fmt: skip
_AWK = Filter(valued="fFv", script=1, script_opts="f", assignments=True)
_HEAD = Filter(valued="ncb", long_valued=dict.fromkeys(("--lines", "--bytes"), 1))
FILTERS: dict[str, Filter] = {
    "cat": Filter(),
    "tac": Filter(),
    "rev": Filter(),
    "nl": Filter(valued="bdfhilnsvw"),
    "tee": Filter(operands="never"),
    "tr": Filter(operands="never"),
    "xargs": Filter(
        valued="EIJLnPRSsda",
        long_valued={"--arg-file": 1},
        operands="never",
        options_end=True,
        no_input="a",
        long_no_input=frozenset({"--arg-file"}),
    ),
    "grep": _GREP,
    "egrep": _GREP,
    "fgrep": _GREP,
    "ugrep": _GREP,
    "sed": Filter(
        valued="ef",
        long_valued=dict.fromkeys(("--expression", "--file"), 1),
        script=1,
        script_opts="ef",
        long_script=frozenset({"--expression", "--file"}),
        no_input="i",
        long_no_input=frozenset({"--in-place"}),
    ),
    "awk": _AWK,
    "gawk": _AWK,
    "mawk": _AWK,
    "nawk": _AWK,
    "jq": Filter(
        valued="fL",
        long_valued={
            "--arg": 2,
            "--argjson": 2,
            "--slurpfile": 2,
            "--rawfile": 2,
            "--indent": 1,
            "--from-file": 1,
        },
        script=1,
        script_opts="f",
        long_script=frozenset({"--from-file"}),
        no_input="nh",
        long_no_input=frozenset({"--null-input"}),
        rest_args=frozenset({"--args", "--jsonargs"}),
    ),
    "sort": Filter(
        valued="kotST",
        long_valued=dict.fromkeys(
            ("--key", "--output", "--field-separator", "--buffer-size", "--temporary-directory"), 1
        ),
        long_no_input=frozenset({"--files0-from"}),
    ),
    "uniq": Filter(valued="fs", long_valued=dict.fromkeys(("--skip-fields", "--skip-chars"), 1)),
    "wc": Filter(long_no_input=frozenset({"--files0-from"})),
    "head": _HEAD,
    "tail": _HEAD,
    "cut": Filter(
        valued="bcdf",
        long_valued=dict.fromkeys(
            ("--bytes", "--characters", "--delimiter", "--fields", "--output-delimiter"), 1
        ),
    ),
}
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
    m = None if isinstance(tok, shell_segments.Literal) else REDIRECT.match(tok)
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
    if prog in FILTERS:
        return prog if _filter_reads(FILTERS[prog], args) else None
    if prog in BUILTIN_READERS:
        flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
        return None if "t" in flags or "u" in flags else prog
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


def _begin_only(program: str) -> bool:
    """Is an awk program BEGIN blocks only (it reads no input)? Braces in strings are rare enough
    in a BEGIN block that counting them is the rule; an unbalanced program is judged a reader."""
    rest = program.strip()
    while rest.startswith("BEGIN"):
        rest = rest[5:].lstrip()
        if not rest.startswith("{"):
            return False
        depth = 0
        for i, ch in enumerate(rest):
            depth += {"{": 1, "}": -1}.get(ch, 0)
            if depth == 0:
                rest = rest[i + 1 :].lstrip(" \t\n;")
                break
        else:
            return False
    return not rest


def _filter_reads(spec: Filter, args: list[str]) -> bool:
    """THE rule: does a filter with this grammar read its stdin given these arguments?"""
    operands: list[str] = []
    scripted = rest = ended = False
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a.startswith("__goh_heredoc_"):
            continue
        if ended or a == "-" or not a.startswith("-"):
            operands.append(a)
            ended = ended or spec.options_end
            continue
        if a == "--":
            ended = True
            continue
        if a.startswith("--"):
            name, glued, _ = a.partition("=")
            if name in INFO or name in spec.long_no_input:
                return False
            scripted = scripted or name in spec.long_script
            rest = rest or name in spec.rest_args
            if not glued:
                i += spec.long_valued.get(name, 0)
            continue
        for j, ch in enumerate(a[1:], 1):
            if ch in spec.no_input:
                return False
            scripted = scripted or ch in spec.script_opts
            if ch in spec.valued:
                if j == len(a) - 1:
                    i += 1  # the value is the next word
                break
    if spec.operands == "never":
        return True
    if not scripted and spec.script:
        program, operands = operands[: spec.script], operands[spec.script :]
        if spec.assignments and program and _begin_only(program[0]):
            return False
    if rest:
        operands = []
    if spec.assignments:
        operands = [o for o in operands if not re.match(r"^[A-Za-z_]\w*=", o)]
    return not operands or "-" in operands


def shell_reads(command: str) -> str | None:
    """The read builtin a command's shell runs itself (`read`, `mapfile`), or None: the reader the
    stall detector cannot see as a process. Fed or not -- the shell's own fd 0 tells that."""
    try:
        segs, bodies = shell_segments.split_ops(command)
    except ValueError:
        return None
    for tokens, _op in segs:
        words = [w for w in tokens if w not in OPENERS and w not in SKIP]
        argv = _words(words, bodies)[0]
        reader = reads_stdin(argv) if argv else None
        if reader in BUILTIN_READERS:
            return reader
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


__all__ = ["ESCAPE", "FILTERS", "Filter", "judge", "reads_stdin", "shell_reads"]
