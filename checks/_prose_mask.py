"""Blank the PROSE of a Python or shell source, keeping its line count 1:1, so a scanner reads code.

    python_code_only(text)  comments and docstring-shaped string statements become spaces
    shell_code_only(text)   comments and the bodies of QUOTED heredocs become spaces

WHY. `check_display_seam.py` refused ZeroThunder's tools/publish_bundle.py because its docstring
said "exec Contents/MacOS/ZeroThunder" (GOH.DISPLAYSEAMDOC). A string that is an ARGUMENT is code
-- `subprocess.run(["osascript", ...])` names its command in a literal -- so only strings that
stand alone as a statement (docstrings, block prose) are blanked, never every literal. A quoted
heredoc (`<<'EOF'`) expands nothing, so its body is text; an unquoted one runs `$(...)`, so it
stays. Python that will not parse is returned unchanged: a mask that guesses is a gate that can
be talked out of a finding. Swift has its own mask in `_swift_text.py`.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize


def _blank(lines, row0, col0, row1, col1):
    """Spaces over [row0:col0, row1:col1) with 1-based rows, preserving every newline."""
    for row in range(row0, row1 + 1):
        line = lines[row - 1]
        start = col0 if row == row0 else 0
        end = col1 if row == row1 else len(line)
        lines[row - 1] = line[:start] + " " * (end - start) + line[end:]


def python_code_only(text):
    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError, ValueError):
        return text
    lines = text.split("\n")
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            # ast columns are UTF-8 byte offsets; re-derive character columns per line.
            row0, row1 = node.lineno, node.end_lineno
            col0 = len(lines[row0 - 1].encode()[: node.col_offset].decode("utf-8", "replace"))
            col1 = len(lines[row1 - 1].encode()[: node.end_col_offset].decode("utf-8", "replace"))
            _blank(lines, row0, col0, row1, col1)
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            (row0, col0), (row1, col1) = tok.start, tok.end
            _blank(lines, row0, col0, row1, col1)
    return "\n".join(lines)


HEREDOC_QUOTED = re.compile(r"<<-?\s*(?:'(\w+)'|\"(\w+)\"|\\(\w+))")


def _strip_comment(line):
    """`line` with a `#` comment removed: one that starts a word outside quotes."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"':
                continue
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t;|&("):
            return line[:i] + " " * (len(line) - i)
    return line


def shell_code_only(text):
    out, closing = [], None
    for line in text.split("\n"):
        if closing is not None:
            out.append(" " * len(line))
            if line.strip() == closing:
                closing = None
            continue
        code = _strip_comment(line)
        heredoc = HEREDOC_QUOTED.search(code)
        if heredoc:
            closing = next(g for g in heredoc.groups() if g)
        out.append(code)
    return "\n".join(out)
