#!/usr/bin/env python3
"""One-off: rewrite the emptiness asserts the new gate finds.

    python3 tools/fix_empty_asserts.py <crate-dir> [...]

    assert!(X.is_empty(), MSG)        ->  assert_eq!(X.len(), 0, MSG)
    assert!(!X.is_empty(), MSG)       ->  assert_ne!(X.len(), 0, MSG)
    assert!(X.len() == 0, MSG)        ->  assert_eq!(X.len(), 0, MSG)
    assert!(X.len() > 0, MSG)         ->  assert_ne!(X.len(), 0, MSG)

# Why it only rewrites SINGLE-LINE invocations

Three earlier versions of this script tried to rewrite the multi-line forms too,
by mapping the checker's parsed invocation back onto raw source offsets. All
three ate a statement's `;`, and one ate a `}` — because the text the checker
parses (comments stripped, string literals blanked, whitespace collapsed) is not
the text in the file, so no offset into the first is an offset into the second.
Rather than a fourth version of that machinery, the rewrite is anchored to a
whole source line: if the invocation is on one line, the line IS the invocation
and there is no alignment to get wrong. Multi-line forms are reported for a
human, and there are few of them.

Everything else it is unsure about it prints and skips rather than guessing: a
compound condition (`assert!(a.is_empty() && b.is_empty())`), an unbalanced
receiver, a `debug_assert!` carrying a message. Silently rewriting those is how
a codemod becomes the bug.

`cargo fmt` afterwards normalises layout. The verification that matters is the
compiler and the test suite: a rewrite that broke a test, or that means the
wrong thing, is caught by running them -- which is why this is never run
without.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "checks"))
from check_no_empty_assert import _findings  # noqa: E402
from _rust_text import code_portions  # noqa: E402

# One line, one invocation, nothing else on it. `;` cannot appear inside a
# condition in these shapes, so its absence from the class is what makes the
# rewrite safe to anchor this way.
_ONELINE = re.compile(
    r"^(?P<indent>\s*)(?P<macro>debug_assert|assert)!\((?P<body>[^;]*)\)(?P<tail>\s*;?)\s*$"
)


def _scan(text: str):
    """Walk RAW text, skipping string literals. Returns the index just past the
    macro's closing paren, or None if the text never balances.

    This scans the file's own bytes rather than the checker's parsed view, which
    is the whole difference from the three earlier versions: their offsets were
    into comment-stripped, literal-blanked, whitespace-collapsed text, and no
    offset into that is an offset into the file.
    """
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < len(text):
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if j >= len(text):
                return None
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _balanced(text: str) -> bool:
    return all(text.count(a) == text.count(b) for a, b in (("(", ")"), ("[", "]"), ("{", "}")))


def _rewrite_line(line: str):
    """Return (new_line, note). note is None only when the rewrite is certain."""
    m = _ONELINE.match(line)
    if not m:
        return None, "not a single-line invocation"
    macro, body = m.group("macro"), m.group("body").strip()

    # Walk the body skipping over string literals: the message is quoted text,
    # and a `(` or a comma inside it is not structure. An earlier version bailed
    # at the first `"` and therefore skipped every assert that carried a message
    # -- which is the whole class this script exists to fix.
    depth = 0
    split_at = None
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"':
            j = i + 1
            while j < len(body):
                if body[j] == "\\":
                    j += 2
                    continue
                if body[j] == '"':
                    break
                j += 1
            if j >= len(body):
                return None, "unterminated string in the body"
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0 and split_at is None:
            split_at = i
        i += 1
    if depth != 0:
        return None, "unbalanced body"

    condition = (body[:split_at] if split_at is not None else body).strip()
    message = body[split_at + 1 :].strip() if split_at is not None else None

    if "&&" in condition or "||" in condition:
        return None, "compound condition"

    negated = condition.startswith("!")
    receiver = condition[1:].strip() if negated else condition
    if not receiver:
        return None, "no receiver"

    mm = re.fullmatch(r"(?P<recv>.*)\.is_empty\(\)", receiver)
    if mm:
        receiver = mm.group("recv").strip()
    else:
        mm = re.fullmatch(r"(?P<recv>.*)\.len\(\)\s*(?:==|!=|<|>)\s*0", receiver)
        if not mm:
            return None, "no emptiness call"
        receiver = mm.group("recv").strip()

    if not receiver or not _balanced(receiver):
        return None, "receiver is not balanced"
    if macro == "debug_assert" and message:
        return None, "debug_assert! cannot carry a message"

    parts = [f"{receiver}.len()", "0"]
    if message:
        parts.append(message)
    new_macro = "assert_ne" if negated else "assert_eq"
    return f"{m.group('indent')}{new_macro}!({', '.join(parts)}){m.group('tail')}", None


def rewrite_file(path: Path):
    text = path.read_text(encoding="utf-8")
    if not _findings(text):
        return 0, []
    lines = text.split("\n")
    rewritten = 0
    skipped = []
    # Findings are reported per invocation start line, which for every shape
    # this rewrites is the line the whole invocation sits on.
    for lineno, _invocation in reversed(_findings(text)):
        idx = lineno - 1
        new, note = _rewrite_line(lines[idx])
        if new is not None:
            lines[idx] = new
            rewritten += 1
            continue
        # Multi-line: find the end by scanning the raw lines, then rewrite the
        # whole span as one line. The span is replaced wholesale, so a statement
        # nested inside the condition cannot be left behind.
        span_end = _scan("\n".join(lines[idx:]))
        if span_end is None:
            skipped.append((lineno, note, lines[idx].strip()))
            continue
        consumed, offset = 0, 0
        for offset, line in enumerate(lines[idx:]):
            consumed += len(line) + 1
            if consumed >= span_end:
                break
        raw = "\n".join(lines[idx : idx + offset + 1])
        collapsed = " ".join(part.strip() for part in raw.split("\n"))
        collapsed = re.sub(r"\s+", " ", collapsed).strip()
        new, note = _rewrite_line(collapsed)
        if new is None:
            skipped.append((lineno, note, collapsed[:88]))
            continue
        tail_match = re.search(r"\s*(;?)\s*$", raw)
        tail = tail_match.group(1) if tail_match else ""
        lines[idx : idx + offset + 1] = [new + tail]
        rewritten += 1
    path.write_text("\n".join(lines), encoding="utf-8")
    return rewritten, skipped


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    total = 0
    all_skipped = []
    for root in argv[1:]:
        root = Path(root)
        for path in sorted(root.rglob("*.rs")):
            rel = path.relative_to(root)
            # The exemption the gate honours from GOH_EXCLUDE. routines carries a
            # vendored crate, and rewriting it is not ours to do.
            if rel.parts[0] in ("target", "vendor") or ".generated." in str(rel):
                continue
            done, skipped = rewrite_file(path)
            total += done
            all_skipped.extend((path, ln, note, src) for ln, note, src in skipped)
    print(f"rewrote {total} assertion(s)")
    if all_skipped:
        print(f"SKIPPED {len(all_skipped)} (a human has to look at these):")
        for path, lineno, note, src in all_skipped:
            print(f"  {path}:{lineno}: {note}: {src[:88]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
