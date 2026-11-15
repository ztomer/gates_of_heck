//! `goh empty-assert`'s row in the shape table (`crate::shapes::table::ROWS`, GOH.CLASS.SHAPE,
//! GOH.EMPTYASSERTSTRING).
//!
//! The probe is a plain grep for an emptiness test inside an `assert!`; the hook is every
//! `assert!`/`debug_assert!` invocation the scanner reads in code, whatever its verdict, through
//! the shared Rust lexer -- so assert text inside a string, a backslash-continued or raw string's
//! later lines included, is not judged, and the row names that shape.

use crate::shapes::table::{COMMENT, DOCS};
use crate::shapes::{Declared, Shape, Token, Verdict};

/// `goh empty-assert`: the first line of every assertion it reads in a compiled Rust source.
///
/// # Errors
///
/// A built-in pattern that does not compile.
pub fn judged(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    if !(rel.as_bytes().ends_with(b".rs") && crate::noallow::is_compiled_src(rel))
        || crate::noallow::is_generated(text)
    {
        return Ok(Vec::new());
    }
    Ok(super::Rules::new()?
        .invocations(text)
        .into_iter()
        .map(|(n, _)| n)
        .collect())
}

/// This scanner's row.
pub const SHAPES: Declared = Declared {
    probe: r"\b(?:debug_)?assert!\s*\(.*\b(?:is_empty|len)\s*\(",
    judged,
    owns: &[Token {
        name: "an emptiness assertion",
        sample: ("src/a.rs", "fn f(v: &[u8]) {\n    assert!(v.is_empty());\n}\n"),
    }],
    sources: &[
        "crates/goh/src/emptyassert.rs",
        "crates/goh/src/emptyassert_shapes.rs",
        "crates/goh/src/rustlex.rs",
    ],
    opt_in: None,
    shapes: &[
        Shape {
            name: "an assert in compiled Rust source",
            path: r"(^|/)(src|benches|tests|examples)/.*\.rs$|(^|/)build\.rs$",
            line: r"assert!",
            verdict: Verdict::Covered,
            fixture: (
                "src/lib.rs",
                "fn f(v: &[u8]) {\n    assert!(v.is_empty(), \"left {v:?}\");\n}\n",
            ),
        },
        Shape {
            name: "a whole-line comment",
            path: "",
            line: COMMENT,
            verdict: Verdict::OutOfScope("a comment quoting an assertion asserts nothing"),
            fixture: ("src/a.rs", "// assert!(v.is_empty()); is prose\nfn f() {}\n"),
        },
        Shape {
            name: "assert text in a string literal, a continued or raw string's later line included",
            path: r"\.rs$",
            line: "\"",
            verdict: Verdict::OutOfScope(
                "a string is data (a fix text, a fixture); nothing asserts it -- the lexer carries the \
                 string across a trailing `\\` and a raw string's lines",
            ),
            fixture: (
                "src/fix.rs",
                "const FIX: &str = \"write \\\n    assert!(x.is_empty()); is the shape\";\n",
            ),
        },
        Shape {
            name: "a cargo-fuzz target",
            path: r"(^|/)fuzz_targets/[^/]*\.rs$",
            line: "",
            verdict: Verdict::OutOfScope(
                "a fuzz target is a nightly-only crate outside the workspace the Rust gate builds; \
                 clippy never lints it, and this rule holds the shapes clippy refuses",
            ),
            fixture: ("fuzz/fuzz_targets/proto.rs", "fn f(b: &[u8]) { assert!(b.len() <= 9); }\n"),
        },
        Shape {
            name: "another language quoting Rust as data",
            path: r"(?i)\.(py|sh|bash|js|mjs|ts|swift|json|jsonl|toml|ya?ml|txt|patch)$",
            line: "",
            verdict: Verdict::OutOfScope(
                "a test in another language holds Rust source as a string it feeds the scanner",
            ),
            fixture: ("tests/test_assert.py", "SRC = \"assert!(v.is_empty());\"\n"),
        },
        Shape {
            name: "prose",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope("a doc names the shape to describe it; it compiles nothing"),
            fixture: ("docs/map.md", "No `assert!(x.is_empty())`.\n"),
        },
    ],
};
