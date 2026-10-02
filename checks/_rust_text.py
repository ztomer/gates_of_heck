"""Shared Rust-source knowledge for the checkers that read Rust as text: comment
and string-literal stripping, which file counts as compiled source, and the
`@generated` marker.

`check_no_allow.py` grew this while chasing suppressions written inside doc
comments and inside string literals (2026-08-25/26, and again 2026-09-23 for
the `cfg_attr` case). Both defects are the same shape — a gate that reads prose
or a fixture as code — and both fixings had to know about the same three things:
`//` tails, nestable `/* */` spans whose state crosses lines, and quoted text.
So the logic lives here once, and the second checker to need it did not write a
fourth copy.

The contract for `code_portions`:

* Yields `(lineno, code)` with 1-indexed line numbers matching the input.
* Whole-line `//` comments (which covers `///` and `//!`), `//` tails and
  `/* */` spans are removed, carrying comment state across lines by depth.
* A second `/*` on one line re-enters comment state, and a real attribute after
  a same-line `*/` close is still returned — so the depth counter has no blind
  spot at either end.
* An unterminated block comment runs safe to EOF: everything after stays
  comment, which is the safe direction for a gate.

String literals are NOT stripped here, because a quoted `#[allow(` is still
worth judging on its own line while a quoted `#[cfg_attr(` must not open state
that leaks to later lines. `literal_free` gives callers that second view; see
`STRING_LITERAL` and its use in `check_no_allow.py`.
"""
import re

# A double-quoted literal, honouring backslash escapes. Enough for Rust: a
# string cannot span lines without a line continuation, so one line is the
# whole extent of one literal for the purposes of "is this text quoted".
STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')


def code_portions(text: str):
    """Yield (lineno, code-only line) with /* */ spans (nestable, left-to-right,
    state carried across lines) and // tails removed."""
    depth = 0
    for lineno, line in enumerate(text.split("\n"), 1):
        kept = []
        i = 0
        while i < len(line):
            if depth == 0 and line.startswith("//", i):
                break  # line comment: rest of the line is prose
            if line.startswith("/*", i):
                depth += 1
                i += 2
                continue
            if depth > 0 and line.startswith("*/", i):
                depth -= 1
                i += 2
                continue
            if depth == 0:
                kept.append(line[i])
            i += 1
        yield lineno, "".join(kept)

# Where a Rust file counts as compiled source. Every compiled Rust file: src/,
# benches/, tests/, examples/ and build.rs. Tests are in scope because clippy
# `-D warnings` runs over them via `--all-targets`, so a defect there is as real
# as one in the library.
def is_compiled_src(rel: str) -> bool:
    if rel == "build.rs" or rel.endswith("/build.rs"):
        return True
    return any(
        f"/{d}/" in rel or rel.startswith(f"{d}/")
        for d in ("src", "benches", "tests", "examples")
    )


# The marker a generator writes. A mention in a code span (`@generated`, as this
# module's own doc and every doc about the policy writes it) is documentation,
# not the marker: counting it exempted the native port's source from itself
# (2026-09-23). Kept next to the scope rule because both answer "is this file
# ours to judge", which is the question every Rust checker here asks first.
GENERATED_MARKER = re.compile(r"(?<!`)@generated(?!`)")


def is_generated_blob(blob: bytes) -> bool:
    """A Rust file is machine-generated iff `@generated` is among its FIRST 40
    LINES. (An earlier implementation read the first 2000 characters instead —
    same intent, different window; the docstring wins.)"""
    head = blob.decode("utf-8", errors="replace").splitlines()[:40]
    return GENERATED_MARKER.search("\n".join(head)) is not None
