"""A Bash tool command's simple commands, the way the shell runs them (GOH.HEREDOCBUILD).

THE CASE. The ZT-1 agent wrote a script with `cat > rg.sh <<'EOF' ... cargo clippy ... EOF` and ran
it with `python3 "$GOH_DIR/tools/load.py" --wait 0 -- bash rg.sh`. The bare-build rule
(`lib/load_bare.py`) refused it: the guard's splitter read the heredoc body as commands, so the
`cargo clippy` the body only WROTE looked like one run bare. A heredoc body is the stdin of the
command that owns it: text for `cat`, `git commit -F -` or `python3 -`, and code only for a shell
that reads its script from stdin (`bash`, `sh -s`).

So this splitter judges the command that runs:
  * a heredoc body is cut out of the command and handed back beside a marker token that stands
    in the owning simple command where its `<<WORD` was (`split`), so a rule decides by the OWNER
    whether the body is code (`reads_script`);
  * a newline separates commands, as `;` does (a script of two lines is two commands, not one
    command with the second's words as arguments), and a backslash-newline joins lines;
  * quoted text stays one word (shlex), and a `#` comment is dropped.

The heredoc finder is `secret_render.spans`, the one that rule already trusts for the same
question. A command shlex cannot split raises ValueError, which every caller treats as "unjudged":
the guard must never be the thing that breaks an ordinary command.
"""

from __future__ import annotations

import os
import re
import shlex

import secret_render

SEPARATORS = {"&&", "||", ";", "|", "&", "\n"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
_WRAPPERS = {"sudo", "doas", "env", "nohup", "exec", "command", "nice", "time", "rtk"}
_MARKER = "__goh_heredoc_{}__"
_OPERATOR = re.compile(r"<<-?\s*(['\"]?)([^\s;&|<>()'\"]+)\1")


def _cut_heredocs(command: str) -> tuple[str, dict[str, str]]:
    """The command with each heredoc body (and its closing line) cut out and its `<<WORD` replaced
    by a marker token, and the bodies by marker.

    Every opener on a line is cut (GOH.HEREDOC2): `cat <<A <<B` and `cmd1 <<A; cmd2 <<B` open two
    bodies that follow the line one after the other, and each body's marker stands where its own
    `<<WORD` was -- cutting only the first left the second body's lines in the text as commands.
    """
    docs = sorted((a, s, e) for a, s, e, kind in secret_render.spans(command) if kind == "<<")
    bodies: dict[str, str] = {}
    out: list[str] = []
    at = 0
    n = 0
    i = 0
    while i < len(docs):
        anchor, start, _ = docs[i]
        if anchor < at:  # a span inside a body already cut
            i += 1
            continue
        # the openers on this line: every heredoc anchored before the first body starts
        line = [d for d in docs[i:] if d[0] < start]
        i += len(line)
        pos = at
        last = None
        for a, s, e in line:
            op = _OPERATOR.match(command, a)
            if op is None:
                continue
            marker = _MARKER.format(n)
            n += 1
            bodies[marker] = command[s:e]
            out += [command[pos:a], f" {marker} "]
            pos = op.end()
            last = e
        if last is None:
            continue
        closing = command.find("\n", last)
        out += [command[pos:start], "\n"]
        at = len(command) if closing < 0 else closing
    out.append(command[at:])
    return "".join(out), bodies


def split_ops(command: str) -> tuple[list[tuple[list[str], str]], dict[str, str]]:
    """(simple commands each with the operator that ENDS it, heredoc bodies by marker).

    The operator is the separator token after the command (`|`, `&&`, `;`, `&`, a newline), or ""
    for the last: a rule that asks whether a command is piped into, or backgrounded, reads it here
    (`lib/stdin_reader.py`, GOH.STDINHANG). Raises ValueError where shlex cannot split the command.
    """
    text, bodies = _cut_heredocs(command.replace("\\\n", " "))
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = "#"
    segs: list[tuple[list[str], str]] = []
    words: list[str] = []
    for tok in lex:
        if tok in SEPARATORS or (tok and set(tok) <= set(";&|\n")):
            op = tok.replace("\n", "") or "\n"  # `|\n` lexes as one token: the `|` is the operator
            if words:
                segs.append((words, op))
            elif segs and op != "\n":
                segs[-1] = (segs[-1][0], op)
            words = []
        else:
            words.append(tok)
    if words:
        segs.append((words, ""))
    return segs, bodies


def split(command: str) -> tuple[list[list[str]], dict[str, str]]:
    """(simple commands, heredoc bodies by marker): a body's marker is a token of its owner.

    Raises ValueError where shlex cannot split the command (an unclosed quote).
    """
    segs, bodies = split_ops(command)
    return [s for s, _ in segs], bodies


def segments(command: str) -> list[list[str]]:
    """The simple commands that run, heredoc bodies left out (they are their owner's stdin)."""
    segs, bodies = split(command)
    kept = [[t for t in seg if t not in bodies] for seg in segs]
    return [s for s in kept if s]


def program(seg: list[str]) -> tuple[str, list[str]]:
    """The program a simple command runs, past `VAR=x` and wrappers (`sudo`, `timeout 5`)."""
    i = 0
    while i < len(seg):
        word = os.path.basename(seg[i])
        if word == "timeout":
            i += 2
        elif word in _WRAPPERS or re.match(r"^\w+=", seg[i]) or (i > 0 and seg[i].startswith("-")):
            i += 1
        else:
            return word, seg[i + 1 :]
    return "", []


def reads_script(seg: list[str]) -> bool:
    """Does this simple command run its stdin as shell code (`bash`, `sh -s`, `bash -x`)?

    A shell given `-c` or a script file reads its stdin as DATA, so a heredoc there is text.
    """
    prog, args = program(seg)
    if prog not in SHELLS:
        return False
    operands = [a for a in args if not a.startswith("-") and not a.startswith("__goh_heredoc_")]
    return "-c" not in args and ("-s" in args or not operands)


__all__ = ["SEPARATORS", "SHELLS", "program", "reads_script", "segments", "split", "split_ops"]
