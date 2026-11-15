//! `file length`'s row in the shape table (`crate::shapes::table::ROWS`, GOH.CLASS.SHAPE,
//! GOH.LINECAPDOCS).
//!
//! A WHOLE-FILE scanner: its verdict is on a file, so its hook judges every line of a file it
//! measures and it owns no line token (`crate::shapes::tests` holds such a row to judging every line
//! of its covered fixtures, so a line scanner cannot pass as one). The probe is the interpreter
//! line, `#!`: the one mark a program carries whatever its file is named, so a script the suffix
//! list does not name shows up beside the ones it does.
//!
//! MARKDOWN IS NOT CAPPED (decided 2026-11-15, GOH.LINECAPDOCS). The cap bounds how much code one
//! change has to hold in view; a doc is read by its headings, and its length is its subject's
//! (`docs/map.md` is one row per tracked file, so it grows with the tree and splitting it would
//! put one lookup in two files). `length.rs` measured source suffixes only from the start; this row
//! is where that exemption is named.

use crate::shapes::table::DOCS;
use crate::shapes::{Declared, Shape, Verdict};

/// `file length`: every line of a measured file, whatever its count.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn judged(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(if crate::length::is_measured(rel, None) {
        (1..=text.lines().count()).collect()
    } else {
        Vec::new()
    })
}

/// Shebang scripts the suffix list does not name (to be filed as a member).
const SCRIPT_GAP: &str = "GOH.LINECAPSCRIPTS";

/// This scanner's row.
pub const SHAPES: Declared = Declared {
    probe: r"^#!",
    judged,
    owns: &[],
    sources: &["crates/goh/src/length.rs", "crates/goh/src/length_shapes.rs"],
    shapes: &[
        Shape {
            name: "a source suffix",
            path: r"\.(rs|py|swift|c|h|cpp|hpp|cc|m|mm|kt|java|go|ts|tsx|js|jsx|sh|bash|rb|mk|cmake)$",
            line: "",
            verdict: Verdict::Covered,
            fixture: ("tools/run.py", "#!/usr/bin/env python3\nprint(1)\n"),
        },
        Shape {
            name: "a build script by its name",
            path: r"(^|/)(Makefile|GNUmakefile|makefile|CMakeLists\.txt|Justfile|justfile)$",
            line: "",
            verdict: Verdict::Covered,
            fixture: ("Makefile", "#!/usr/bin/make -f\nall:\n\ttrue\n"),
        },
        Shape {
            name: "Markdown and prose docs",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope(
                "a doc is read by its headings and is as long as its subject; the cap bounds the code \
                 one change holds in view (docs/map.md is one row per tracked file)",
            ),
            fixture: ("docs/map.md", "# Map\n\n```sh\n#!/bin/sh\n```\n"),
        },
        Shape {
            name: "data: a patch, a log, a recorded output, a JSON record",
            path: r"(?i)\.(patch|diff|log|snap|golden|csv|tsv|out|expected|json|jsonl)$",
            line: "",
            verdict: Verdict::OutOfScope("a recording is as long as what it recorded; nobody edits it as code"),
            fixture: ("tests/fixtures/run.log", "#!/bin/sh\nset -e\n"),
        },
        Shape {
            name: "a script known by its shebang, or a suffix the list lacks (.zsh, .tmpl, .template, .pl, .mjs)",
            path: r"(^|/)[^./]+$|(?i)\.(zsh|tmpl|template|pl|mjs|cjs|kts|metal|lua)$",
            line: "",
            verdict: Verdict::Gap(SCRIPT_GAP),
            fixture: ("bin/ab_test", "#!/usr/bin/env python3\nprint(1)\n"),
        },
    ],
};

#[cfg(test)]
mod tests {
    #[test]
    fn the_shape_row_names_markdown_and_the_scanner_does_not_measure_it() {
        assert!(!crate::length::is_measured("docs/map.md", None));
        let lines = super::judged("docs/map.md", "a\n".repeat(600).as_str()).unwrap_or_default();
        assert!(lines.is_empty(), "a doc is judged: {} lines", lines.len());
    }
}
