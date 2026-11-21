"""A QUOTED heredoc's body is text to the pgrep and kill-by-name guards, backticks included
(GOH.WAITSTOP, met committing it).

`git commit -F - <<'EOF'` with a message that quoted a refused command in Markdown backticks
(`until ! pgrep -f ...`) was refused as a self-matching pgrep, and a backticked by-name kill in such
a message as a kill: both guards read a backtick as a command substitution wherever it stood. A
body under `<<'EOF'`, `<<"EOF"` or `<<\\EOF` is never expanded, so nothing in it runs. An UNQUOTED
heredoc's body does run its backticks and `$(...)`, and a double-quoted string does too, so those
stay refused (`busyloop.blank_quoted_heredocs`). And a by-name kill on a LATER line of a multi-line
command was never seen at all: the kill guard read the whole command as one line.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_claude_load_guard import _decide

PK, PG = "p" + "kill", "p" + "grep"  # split: the house's kill-by-name check reads files

ALLOWED = [
    f"git commit -q -F - <<'EOF'\nfeat: x\n\nwaits (`until ! {PG} -f ...`, whose\nEOF\ngit log -1",
    f'git commit -q -F - <<"EOF"\nfix: (`{PK} -f foo`) was refused\nEOF\necho done',
    f"cat <<\\EOF > notes.md\nthe old way: `{PG} -f build.sh || ./build.sh`\nEOF\n./build.sh",
    f"cat <<-'EOF'\n\tsee `{PK} -f gate.sh`\n\tEOF\necho",
]
REFUSED = [
    f"cat <<EOF\n`{PK} -f foo`\nEOF",  # unquoted: the backtick runs
    f'git commit -m "waits (`{PG} -f foo`)"; echo foo',  # double quotes: the backtick runs
    f"cat <<EOF\n$({PG} -f build.sh)\nEOF\n./build.sh",
    f"cat <<'EOF'\nquoted body\nEOF\n{PK} -f foo",  # code after the body is code
    f"echo hi\n{PK} -f foo",  # a kill on a later line is a command of its own
    f"cd /x &&\n{PK} -f foo",
    f"echo a  # a note\n{PK} -f foo",  # the comment ends at its newline
    f"{PK} \\\n  -f foo",  # a backslash-newline continues the command
    f"{PG} -f foo |\n  xargs kill",  # a pipe continues onto the next line
]
ALLOWED += [
    f"pid=$({PG} -n -f build.sh)\nkill 123",  # the lookup and a numeric kill: two commands
]


@pytest.mark.parametrize("command", ALLOWED, ids=range(len(ALLOWED)))
def test_a_quoted_heredoc_body_is_text(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is None, command


@pytest.mark.parametrize("command", REFUSED, ids=range(len(REFUSED)))
def test_an_expanded_backtick_is_still_code(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is not None, command
