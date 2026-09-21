#!/usr/bin/env python3
"""Fail if any git-tracked Rust source contains an `#[allow(...)]` or
`#[expect(...)]` attribute.

Policy (user, 2026-08-02; `#[expect]` added 2026-09-20): clippy runs at
`-D warnings` everywhere, and warnings are FIXED, not silenced. An
`#[allow(...)]` suppresses the lint so it never fires, which defeats the
gate; an `#[expect(...)]` errors when the lint stops firing, which keeps it
from rotting, but it still ships the finding instead of fixing it - a cast
that "fits" today, a function that is "only 104 lines". So both attributes,
outer or inner, are failures. A file is exempt only if it is machine-generated
and carries the standard `@generated` marker (see below); hand-written code
never is. Scope is every compiled Rust file INCLUDING tests: a
`#![allow(dead_code)]` on a shared test fixture is the same suppression with
a different excuse (make the fixture a dev-dependency crate instead).

    python3 checks/check_no_allow.py                    # all tracked Rust files (CI gate)
    python3 checks/check_no_allow.py --staged           # only staged files (pre-commit)
    python3 checks/check_no_allow.py --exclude '^vendor/' # skip a vendored tree

Exclusions come from --exclude (a regex on repo-relative paths), wired from
GOH_EXCLUDE in .gatesrc by gates/rust_gate.sh -- the same exemption the
structural checks honour, so a third-party crate carried in-tree is exempt
from every house check with one line, not policed by this one alone.

Exemption: a Rust file is treated as generated iff it contains `@generated`
within its first 40 lines of comments (the de-facto marker used by prost/tonic,
bindgen and friends). Nothing else exempts a file. This is deterministic and
auditable, the same way check_no_emoji.py's codepoint ranges are.

The checker greps for the literal tokens `#[allow(`, `#![allow(`, `#[expect(`
and `#![expect(` — never a looser regex — and, since 2026-09-21, for the
same suppressions wrapped in `cfg_attr`: `#[cfg_attr(<cfg>, allow(...))]`
and `#![cfg_attr(<cfg>, expect(...))]`. A `cfg_attr` is the attribute with
a condition on it; monitor carried nine `unsafe_code` and cast suppressions
that way, invisible to the literal grep. The wrapped form is matched by the
`allow(`/`expect(` token appearing on a line whose code contains
`cfg_attr(`, which is only ever true of a wrapped suppression (a `cfg_attr`
does nothing else with those tokens) and holds across the multi-line
formatting rustfmt gives it.

Conservative comment-awareness (2026-08-25, depth-counter rewrite 2026-08-26):
a doc-comment MENTIONING #[allow] is prose about the policy, not a
suppression, and a gate that flags its own documentation trains people to
ignore it. Whole-line // comments (covering /// doc comments and //! inner
docs), // tails, and /* */ spans are stripped by a left-to-right depth
counter that tracks OPENERS and CLOSERS per line and carries nesting state
across lines — so a second `/*` on one line re-enters comment state (no false
positive on comment-only mentions) and a real attribute after a same-line
`*/` close is still searched (no blind spot). Unterminated blocks run safe to
EOF: everything after stays comment.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, listed_files, repo_root  # noqa: E402

_GENERATED_MARKER = "@generated"
_ALLOW_PATTERN = re.compile(r"#!?\[(?:allow|expect)\(")
# `#[cfg_attr(<cfg>, allow(...))]`: rustfmt puts the wrapped attribute on its
# own line, so the two tokens are matched across the open attribute rather
# than on one line (see the module doc).
_CFG_ATTR_OPEN = re.compile(r"#!?\[cfg_attr\(")
_WRAPPED_SUPPRESSION = re.compile(r"(?<![\w:])(?:allow|expect)\(")
_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"')


def _code_portions(text: str):
    """Yield (lineno, code-only line): /* */ spans (nestable, left-to-right,
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


def _is_compiled_src(rel: str) -> bool:
    # Every compiled Rust file: src/, benches/, tests/, examples/ and build.rs.
    # Tests are in scope since 2026-09-20 - clippy -D warnings runs over them
    # via --all-targets, so a suppression there defeats the gate just the same.
    if rel == "build.rs" or rel.endswith("/build.rs"):
        return True
    return any(f"/{d}/" in rel or rel.startswith(f"{d}/") for d in ("src", "benches", "tests", "examples"))


def _files(root: str, staged: bool, exclude):
    return [
        f
        for f in listed_files(root, staged=staged)
        if f.endswith(".rs") and _is_compiled_src(f)
        and not (exclude and exclude.search(f))
    ]


def _is_generated(root: str, rel: str, staged: bool) -> bool:
    """The documented policy: `@generated` among the FIRST 40 LINES exempts
    the file. (An earlier implementation read the first 2000 characters
    instead — same intent, different window; the docstring wins.)"""
    blob = content_bytes(root, rel, staged=staged)
    if blob is None:
        return False
    head_lines = blob.decode("utf-8", errors="replace").splitlines()[:40]
    return _GENERATED_MARKER in "\n".join(head_lines)


def _scan(root: str, paths, staged: bool):
    hits = []
    for rel in paths:
        blob = content_bytes(root, rel, staged=staged)
        if blob is None or _is_generated(root, rel, staged):
            continue
        text = blob.decode("utf-8", errors="replace")
        in_cfg_attr = 0  # bracket depth of an open `#[cfg_attr(` attribute
        for lineno, code in _code_portions(text):
            if _ALLOW_PATTERN.search(code):
                hits.append(f"{rel}:{lineno}: {code.strip()}")
                continue
            opened = in_cfg_attr == 0 and _CFG_ATTR_OPEN.search(code)
            code_after = code[opened.start():] if opened else code
            if opened or in_cfg_attr:
                literal_free = _STRING_LITERAL.sub('""', code_after)
                if _WRAPPED_SUPPRESSION.search(literal_free):
                    hits.append(f"{rel}:{lineno}: {code.strip()}")
                # the attribute ends when its brackets balance: the opening
                # `#[` counts here, so the depth returns to 0 on its `)]`.
                in_cfg_attr = max(0, in_cfg_attr + literal_free.count("[") - literal_free.count("]"))
    return hits


def _has_rust(root: str, staged: bool) -> bool:
    """Does this repo contain Rust at all? A tracked Cargo.toml is the manifest that says so."""
    return any(
        f == "Cargo.toml" or f.endswith("/Cargo.toml")
        for f in listed_files(root, staged=staged)
    )


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--staged", action="store_true")
    ap.add_argument("--exclude", default="",
                    help="regex on repo-relative paths to skip (GOH_EXCLUDE)")
    args = ap.parse_args()
    staged = args.staged
    exclude = re.compile(args.exclude) if args.exclude else None
    root = repo_root()
    files = _files(root, staged, exclude)
    hits = _scan(root, files, staged)
    if hits:
        scope = "staged" if staged else "tracked"
        print(f"✗ [{'no_allow'}] {len(hits)} #[allow]/#[expect] in {scope} Rust source:")
        for h in hits[:40]:
            print(f"    {h}")
        print("fix the finding properly; do not add #[allow] or #[expect]. Generated files must carry @generated.")
        sys.exit(1)
    # A repo with no Rust at all genuinely has nothing to police, and failing there would make
    # this gate unadoptable by every non-Rust repo that runs the shared layer. But a repo that
    # HAS Rust and whose scan found none of it is blind: the crate moved, or the src/ + benches/
    # + build.rs scope stopped matching its layout, and "0 files clean" reads identically to a
    # spotless crate. Conditioned on the manifest, so the two cases stay distinguishable.
    if not files and _has_rust(root, staged):
        print("✗ [no_allow] this repo has Rust (a Cargo.toml is tracked) and the scan matched "
              "NO compiled source. That is not a clean run -- the crate layout moved out from "
              "under src/, benches/ and build.rs.", file=sys.stderr)
        sys.exit(1)
    print(f"✓ [no_allow] OK — {len(files)} files clean")


if __name__ == "__main__":
    main()
