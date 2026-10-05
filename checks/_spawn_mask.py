"""Comment, string-literal and heredoc blanking for the three languages `check_no_unreaped_spawn.py` reads.

    checks/_spawn_mask.py            # nothing to run; import it

EXTRACTED rather than written twice. The masking logic and the language rules have no call
site in common: the rules take ALREADY-MASKED text (so a probe can hand them a fixture
verbatim), and only this module knows how to produce it. That is why the cut is here and not
one line either side of it.

ONE PASS, COMMENTS AND STRINGS TOGETHER. Stripping comments first and strings second is the
obvious order and it is wrong twice: a `#` inside a string then eats real code, and a `//`
inside a string opens a comment that swallows the rest of the file. Both were live here, and
the second is the R6 shape -- a gate reading a fixture as code -- reached from the most likely
direction, because this gate is pointed at test files and test files are full of fixtures.

EVERY function preserves line count and offsets, so a finding can be reported as the line the
author actually wrote. That is the whole reason these return a string instead of a list of
hits: a checker that renumbers its own findings as it filters them cannot be trusted with a
line number.
"""

import re

# ── the shared masker: comments AND string literals, one pass, line-preserving ──
# A checker that reads a doc comment or a fixture string as code is the R6 failure, and this
# checker is pointed at test files, which is where fixtures live. Blanking both in ONE pass (rather
# than stripping comments then strings) is what stops a `#` inside a string from eating real code
# and a `//` inside a string from opening a comment that swallows the rest of the file.
_PY_STR_OPEN = re.compile(r"([rRbBfFuU]{0,2})(\"\"\"|'''|\"|')")
# Rust opens a raw string with `r`, `br` or `cr` (byte-raw, C-raw) plus any number of `#`. All
# three prefix forms are here because the first version read `b?r` only, and `cr#"` desynchronised
# the same way `r#"` did.
_RS_RAW_OPEN = re.compile(r"[bcr]?r(#*)\"")
_RS_STR_OPEN = re.compile(r"[bc]?\"")


def mask_py(text: str) -> str:
    """Comments and string bodies replaced by spaces; newlines and offsets preserved.

    THE PREFIX IS PART OF THE OPENER and is matched before the quote, not at it: matched only at a
    quote, the prefix group is always empty and `rb"…"` leaves a stray `rb` in the masked text.

    A BACKSLASH ESCAPES THE CLOSING QUOTE EVEN IN A RAW LITERAL, and this is the opposite of Rust,
    so it was measured against CPython rather than reasoned about: `r"a\""` is ONE string whose
    content is `a\"`, and `r'''a\'''` is a SyntaxError because the `\'` swallows the first quote.
    Rawness changes the CONTENT (`r"a\nb"` keeps both characters) and not the CLOSING rule. A first
    attempt branched on `r` and blanked four real literals in the estate's shape; the interpreter
    said so, and the branch is gone. `--edition 2024` rustc agrees with CPython on nothing here,
    which is why the Rust arm below keeps its own flag.
    """
    out = []
    i, n, quote, escape = 0, len(text), "", False
    while i < n:
        ch = text[i]
        if quote:
            if escape:
                escape = False
                out.append("\n" if ch == "\n" else " ")
                i += 1
                continue
            if ch == "\\":
                escape = True
                out.append(" ")
                i += 1
                continue
            if text.startswith(quote, i):
                out.append(" " * len(quote))
                i += len(quote)
                quote = ""
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        # The pattern needs a quote straight after the prefix, so an identifier like `fn`, `return`
        # or `for` cannot match it.
        m = _PY_STR_OPEN.match(text, i)
        if m:
            quote = m.group(2)
            out.append(" " * len(m.group(0)))
            i += len(m.group(0))
            continue
        if ch in "\"'":
            out.append(" ")
            i += 1
            continue
        out.append(ch)
        i += 1
    out.append(" " * (len(quote)))
    return "".join(out)


def mask_rust(text: str) -> str:
    """`//`, nestable `/* */` and every string form, blanked; newlines preserved.

    Char literals are left ALONE: `'a` is a LIFETIME far more often than a character, and blanking
    `'static` would break the one thing the compiler cares about here.

    THE CLOSING DELIMITER IS NOT THE OPENING TOKEN. The first version stored the whole opener --
    `r"`, `r#"`, `br#"#` -- as the sentinel to match at the close, and a raw string's own closing
    delimiter is only the quote plus its hashes. `r#"{"op":"shutdown"}"#` therefore never closed,
    and the scanner blanked every remaining line of the file: measured on `routines`, NINE spawn
    sites invisible to this gate, and two files that leaked a five-minute `sleep` reported clean.
    A backslash also cannot escape inside a Rust raw string, so it must not arm the escape state
    either -- the second half of the same bug, and `r"a\\\\"` desynchronised for exactly that
    reason (the closing quote was swallowed as an escaped character). State is
    now `(close, raw)`: the delimiter that must be matched, and whether a backslash means anything.
    """
    out = []
    i, n, depth, close, escape, raw = 0, len(text), 0, "", False, False
    while i < n:
        if close:
            if escape:
                escape = False
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
                continue
            if text[i] == "\\" and not raw:
                escape = True
                out.append(" ")
                i += 1
                continue
            if text.startswith(close, i):
                out.append(" " * len(close))
                i += len(close)
                close = ""
                continue
            out.append("\n" if text[i] == "\n" else " ")
            i += 1
            continue
        if depth:
            if text.startswith("*/", i):
                depth -= 1
                out.append("  ")
                i += 2
                continue
            if text.startswith("/*", i):
                depth += 1
                out.append("  ")
                i += 2
                continue
            out.append("\n" if text[i] == "\n" else " ")
            i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if text.startswith("/*", i):
            depth = 1
            out.append("  ")
            i += 2
            continue
        m = _RS_RAW_OPEN.match(text, i)
        if m:
            close = '"' + m.group(1)
            raw = True
            out.append(" " * len(m.group(0)))
            i += len(m.group(0))
            continue
        m = _RS_STR_OPEN.match(text, i)
        if m:
            close = '"'
            raw = False
            out.append(" " * len(m.group(0)))
            i += len(m.group(0))
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def mask_shell(text: str) -> str:
    """Comments, quoted spans and HEREDOC bodies. The heredoc arm is the one that matters: a body
    holding a `&` is data, and reading it as a background launch is a finding invented by the
    checker."""
    lines = text.split("\n")
    out = []
    heredoc = None
    for line in lines:
        if heredoc is not None:
            out.append(" " * len(line))
            if line.strip() == heredoc:
                heredoc = None
            continue
        code = []
        i, n, quote = 0, len(line), ""
        while i < n:
            ch = line[i]
            if quote:
                if ch == quote:
                    quote = ""
                    code.append(" ")
                else:
                    code.append(" ")
            elif ch in "\"'":
                quote = ch
                code.append(" ")
            elif ch == "#" and (not code or code[-1] in " \t;|&("):
                code.extend(" " * (n - i))
                break
            else:
                code.append(ch)
            i += 1
        joined = "".join(code)
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", joined)
        if m:
            heredoc = m.group(1)
        out.append(joined)
    return "\n".join(out)


MASK = {".rs": mask_rust, ".py": mask_py, ".sh": mask_shell, ".bash": mask_shell}
LANGS = tuple(MASK)
