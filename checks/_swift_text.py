"""Swift source with comments and string literals masked -- the one Swift text reader the
remaining Python gates share (`check_display_seam.py`). It came from the retired
`check_no_screen_presentation.py` (now `goh screen`, `crates/goh/src/screen.rs`) when Phase N3
deleted that checker; the native port carries its own copy of the same contract.
"""

from __future__ import annotations

_CODE, _LINE_COMMENT, _BLOCK_COMMENT, _STRING = range(4)


def swift_code_only(text):
    """Swift source with comments and string literals masked to spaces.

    LINE-PRESERVING contract: newlines always survive untouched (callers
    index the result positionally against text.split("\n") — never
    splitlines(), which also breaks on \v \f \x1c-\x1e \x85 U+2028 U+2029
    and can desync when masking erases one inside a literal); everything
    else inside a comment or literal becomes a space. Handles \\" escapes
    (the escaped quote stays in-string) and triple-quoted multi-line
    literals. Block comments nest, per Swift's own grammar.
    """
    out = list(text)
    n = len(text)
    i = 0
    state = _CODE
    comment_depth = 0
    while i < n:
        ch = text[i]
        if state == _CODE:
            if ch == "/" and text.startswith("//", i):
                out[i] = out[i + 1] = " "
                state = _LINE_COMMENT
                i += 2
            elif ch == "/" and text.startswith("/*", i):
                out[i] = out[i + 1] = " "
                state = _BLOCK_COMMENT
                comment_depth = 1
                i += 2
            elif ch == '"':
                if text.startswith('"""', i):
                    end = text.find('"""', i + 3)
                    stop = n if end == -1 else end + 3
                    for k in range(i, stop):
                        if text[k] != "\n":
                            out[k] = " "
                    i = stop
                else:
                    j = i + 1
                    while j < n:
                        cj = text[j]
                        if cj in ('"', "\n"):
                            break
                        if cj == "\\":
                            j += 1
                            # an escaped newline does not continue a
                            # single-line literal — let the loop see it
                            if j < n and text[j] != "\n":
                                j += 1
                            continue
                        j += 1
                    stop = j + 1 if j < n and text[j] == '"' else j
                    for k in range(i, stop):
                        if text[k] != "\n":
                            out[k] = " "
                    i = stop
            else:
                i += 1
        elif state == _LINE_COMMENT:
            if ch == "\n":
                state = _CODE
            else:
                out[i] = " "
            i += 1
        elif state == _BLOCK_COMMENT:
            # Swift nests block comments, so the comment ends at the `*/` that
            # closes the OUTERMOST `/*` — the first `*/` used to end it and the
            # rest of the outer comment was policed as code (2026-09-23).
            if text.startswith("/*", i):
                out[i] = out[i + 1] = " "
                comment_depth += 1
                i += 2
            elif text.startswith("*/", i):
                out[i] = out[i + 1] = " "
                comment_depth -= 1
                if comment_depth == 0:
                    state = _CODE
                i += 2
            else:
                if ch != "\n":
                    out[i] = " "
                i += 1
        else:  # unreachable by construction; keep the shape total
            i += 1
    return "".join(out)
