#!/usr/bin/env python3
"""Fail if any git-tracked Rust source asserts emptiness the way clippy's
`assert_is_empty` and `len_zero` lints say not to.

    python3 checks/check_no_empty_assert.py                    # all tracked Rust files (CI gate)
    python3 checks/check_no_empty_assert.py --staged           # only staged files (pre-commit)
    python3 checks/check_no_empty_assert.py --exclude '^vendor/' # skip a vendored tree
    python3 checks/check_no_empty_assert.py --probe           # prove this gate can go red

# Why this exists when clippy already has the lint

Because clippy does not fire on half the shape. Measured against clippy
1.99.0 on 2026-10-02, one shape per line, with the lint reported by clippy
itself rather than read from its docs:

| written                                              | clippy says        |
|-----------------------------------------------------|--------------------|
| `assert!(v.is_empty());`                             | `assert_is_empty`  |
| `assert!(!v.is_empty());`                            | `assert_is_empty`  |
| `assert!(v.len() == 0);` / `assert!(0 == v.len());`   | `len_zero`         |
| `assert!(v.len() > 0);`                              | `len_zero`         |
| `debug_assert!(v.is_empty());`                       | `assert_is_empty`  |
| `assert!(s\\n    .as_bytes()\\n    .is_empty());`      | `assert_is_empty`  |
| **`assert!(v.is_empty(), "with a message");`**        | **nothing**        |
| **`assert!(!v.is_empty(), "msg");`**                  | **nothing**        |
| `assert_eq!(v.len(), 0);`                            | nothing (correct)  |
| `assert_ne!(v.len(), 0);`                            | nothing (correct)  |
| `let x = v.is_empty(); assert!(x);`                  | nothing (indirect) |

The two bold rows are the reason this checker is not a duplicate of a lint. An
assert carrying a message is the form most people write on purpose — a failure
line that says which value was empty is worth having — and clippy is silent on
every one of them. So an author who writes good assertions gets a clean run
and ships the finding, and the class recurs for reasons that have nothing to do
with discipline.

The last two unflagged rows are the reason this checker is quiet about them:
`assert_eq!(v.len(), 0)` is not a violation, it is clippy's own suggestion for
the first row, and `assert_ne!` is the honest way to say "not empty" without
the negation. A checker that flagged those would be flagging the fix.

# Why it is also not clippy

Two reasons, both about WHEN. Clippy needs the crate to compile, so it runs at
the end of the gate: a violation costs a full re-run and a sweep of every file
it touched, which is what this class has actually cost. Four repos, in two days:
media_server 77 files, routines 50 assertions, app_updates 17, gates_of_heck
24 — every one of them a release blocker rather than a broken build, and
media_server's `bump.sh` refused to cut for exactly this reason. Reading the
staged diff costs a second and reports the offending lines, so the price of a
violation is proportional to the violation instead of to the repository.

Second: a text pass is what closes the table's gap. The rows clippy misses are
invisible to a lint that needs type information and match only on the bare
macro; they are unremarkable to read as text.

What this checker gives up, honestly: it is not a type checker. It matches
shapes, so it cannot see through a bound variable (`let x = v.is_empty();`) and
it does not know whether a receiver has an `is_empty` method at all. It is a
cheap pre-commit pass and a full-tree report, and clippy remains the authority.
Running both is the point; either alone leaves a hole.

Exclusions come from --exclude (a regex on repo-relative paths), wired from
GOH_EXCLUDE in .gatesrc by gates/rust_gate.sh, the same exemption every other
house check honours, so a vendored tree is exempt from all of them with one
line rather than by this one checker's opinion.

A Rust file is treated as machine-generated iff it carries `@generated` within
its first 40 lines, the same marker and the same window `check_no_allow.py`
uses; see that module for why a mention in a code span does not count.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tui.lib import err, ok
except ImportError:  # outside the house PYTHONPATH: plain marks, same wording
    def ok(msg):
        print(f"✓ {msg}")

    def err(msg):
        print(f"✗ {msg}", file=sys.stderr)
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402
from _rust_text import (  # noqa: E402
    STRING_LITERAL,
    code_portions,
    is_compiled_src,
    is_generated_blob,
)

# `assert!` and `debug_assert!` — and deliberately NOT `assert_eq!`/`assert_ne!`,
# which are the forms clippy tells you to write. The boundary lookbehind keeps a
# crate's own `my_assert!(...)` out of the sweep.
_ASSERT_MACRO = re.compile(r"(?<![\w:])(?:debug_)?assert!\s*\(")
# The emptiness call. The leading `!` is OPTIONAL and load-bearing: clippy
# reports the negated form too, and a first version of this pattern made the `!`
# mandatory, so it found every `assert!(x.len() == 0)` and missed every
# `assert!(x.is_empty())` -- the shape the class is actually made of. The
# measured table below is what caught it.
_IS_EMPTY = re.compile(r"(?:!\s*)?\.\s*is_empty\s*\(\s*\)")
# The other lint's shape. Kept as two pieces rather than one ordered regex
# because the comparison can be written either way round (`x.len() == 0` and
# `0 == x.len()` are both reported) and `<`/`>` carry the negated case.
_LEN_CALL = re.compile(r"\.len\s*\(\s*\)")
_ZERO_COMPARE = re.compile(r"(?:==|!=|<|>)\s*0\b|\b0\s*(?:==|!=|<|>)")


def _is_violation(invocation: str) -> bool:
    """Is one whole macro invocation an emptiness assertion in a bad shape?

    `invocation` is the invocation's text with string literals already blanked,
    so a message can never contain the evidence and a `")"` in a message cannot
    end the search early.
    """
    if _IS_EMPTY.search(invocation):
        return True
    # A length compared against zero, in either order. Spelled as two searches
    # so neither ordering has to be enumerated in a regex, and so `x.len() > 0`
    # is caught -- clippy reports that one too.
    return bool(_LEN_CALL.search(invocation) and _ZERO_COMPARE.search(invocation))


def _findings(text: str):
    """Return (lineno, invocation) for each emptiness assertion in one file.

    The macro invocation is accumulated to its CLOSING paren across lines,
    because rustfmt breaks the chain form across lines and a line-local match
    would miss exactly the shape this has to catch:

        assert!(s
            .as_bytes()
            .is_empty());

    Returns the invocation rather than the source line, so a finding carries
    its own evidence even when it starts three lines above the call.
    """
    out = []
    depth = 0
    buf: list[str] = []
    start = 0
    for lineno, code in code_portions(text):
        # Blank literals BEFORE the scan: quoted text is not code, and a quoted
        # paren must not move the depth.
        free = STRING_LITERAL.sub('""', code)
        i = 0
        while i < len(free):
            if depth == 0:
                # Outside a macro, characters are only scanned for the NEXT
                # opener. They must not move the paren depth: counting every `(`
                # in the line made `my_assert!(v.is_empty())` report as a
                # finding, attributed to a line that does not exist, because the
                # depth opened on a macro nobody captured.
                m = _ASSERT_MACRO.match(free, i)
                if not m:
                    i += 1
                    continue
                start, depth, buf = lineno, 1, [m.group(0)]
                i = m.end()
                continue
            ch = free[i]
            buf.append(ch)
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    joined = "".join(buf)
                    if _is_violation(joined):
                        out.append((start, joined))
                    buf = []
            i += 1
        if depth > 0:
            # Carry the invocation across the line break with a space, so a
            # call split before the dot cannot join into a different token.
            buf.append(" ")
    return out


def _is_generated(root: str, rel: str, staged: bool) -> bool:
    blob = content_bytes(root, rel, staged=staged)
    return blob is not None and is_generated_blob(blob)


def _scan(root: str, paths, staged: bool):
    hits = []
    for rel in paths:
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or _is_generated(root, rel, staged):
            continue
        for lineno, code in _findings(blob.decode("utf-8", errors="replace")):
            # One line per finding: the invocation is accumulated across lines,
            # so its own indentation would otherwise print as a ragged block.
            hits.append(f"{rel}:{lineno}: {' '.join(code.split())}")
    return hits


def _files(root: str, staged: bool, exclude):
    return [
        f
        for f in listed_files(root, staged=staged)
        if f.endswith(".rs")
        and is_compiled_src(f)
        and not (exclude and exclude.search(f))
    ]


def _has_rust(root: str, staged: bool) -> bool:
    return any(
        f == "Cargo.toml" or f.endswith("/Cargo.toml")
        for f in listed_files(root, staged=staged)
    )


def probe() -> int:
    """The measured clippy table, run as a self-proof.

    Every case below is one line of the table in this module's docstring, which
    is itself one shape per line measured against clippy 1.99.0 on 2026-10-02.
    The `want=False` rows matter as much as the `want=True` ones: a checker that
    flagged `assert_eq!(v.len(), 0)` would be flagging the fix, and the run that
    would have caught that is this one.

    It is a probe rather than only a unit test because it is what
    `check_probes_pass.py` runs in every repo that adopts this gate, which is the
    half that cannot be skipped: a checker whose match list was narrowed to
    nothing would still exit 0 in CI, and a gate that scans nothing is
    indistinguishable from a gate that scanned everything.
    """
    cases = [
        # (source, want_a_finding)
        ("assert!(v.is_empty());", True),
        ("assert!(!v.is_empty());", True),
        ("assert!(v.len() == 0);", True),
        ("assert!(0 == v.len());", True),
        ("assert!(v.len() > 0);", True),
        ("debug_assert!(v.is_empty());", True),
        ('assert!(v.is_empty(), "with a message");', True),
        ('assert!(!v.is_empty(), "msg");', True),
        ("assert!(s\n    .as_bytes()\n    .is_empty());", True),
        ('assert!(s.as_bytes().is_empty(),\n    "split message");', True),
        ("assert_eq!(v.len(), 0);", False),
        ("assert_eq!(v.to_vec(), Vec::<u8>::new());", False),
        ("assert_ne!(v.len(), 0);", False),
        ("let x = v.is_empty(); assert!(x);", False),
        ("assert!(v.iter().next().is_none());", False),
        ("my_assert!(v.is_empty());", False),
        ("a::assert!(v.is_empty());", False),
        ("assert_eq!(v.len(), 0usize);", False),
        ("// assert!(v.is_empty()); is prose\nfn f() {}\n", False),
        ("/* assert!(v.is_empty()); */\nfn f() {}\n", False),
        ('const S: &str = "assert!(v.is_empty());";\nfn f() {}\n', False),
    ]
    bad = 0
    for source, want in cases:
        got = bool(_findings(source))
        label = source.replace(chr(10), " ")[:56]
        if got != want:
            err(f"probe: {label} (wanted a finding={want}, got {got})")
            bad += 1
        else:
            ok(f"probe: {label} -> {'finding' if got else 'clean'}")
    if bad:
        err(f"check_no_empty_assert --probe: {bad} case(s) wrong")
        return 1
    ok(f"check_no_empty_assert --probe: {len(cases)} measured shapes agree with clippy "
       "1.99.0, and the forms clippy tells you to write stay clean")
    return 0


def main():
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="", help="regex on repo-relative paths (GOH_EXCLUDE)")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args()
    if args.probe:
        return probe()
    staged = args.staged
    exclude = re.compile(args.exclude) if args.exclude else None
    root = repo_root()
    files = _files(root, staged, exclude)
    hits = _scan(root, files, staged)
    if hits:
        scope = "staged" if staged else "tracked"
        print(f"✗ [no_empty_assert] {len(hits)} emptiness assert(s) in {scope} Rust source:")
        for h in hits[:40]:
            print(f"    {h}")
        print(
            "write `assert_eq!(x.len(), 0)` (or `assert_ne!(x.len(), 0)` for the\n"
            "negated case) — those are clippy's own suggestions and are not flagged.\n"
            "To fix a whole tree at once: cargo clippy --fix --all-targets -- -A clippy::pedantic"
        )
        sys.exit(1)
    if not files and _has_rust(root, staged):
        print(
            "✗ [no_empty_assert] this repo has Rust (a Cargo.toml is tracked) and the scan "
            "matched NO compiled source. That is not a clean run -- the crate layout moved "
            "out from under src/, benches/ and build.rs.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"✓ [no_empty_assert] OK — {len(files)} files clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())