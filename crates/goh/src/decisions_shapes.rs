//! `every decision record is whole`'s row in the shape table (`crate::shapes::table::ROWS`).
//!
//! A WHOLE-FILE scanner: its verdict is on a record, so its hook judges every line of a file named
//! as one (`docs/decisions/D-NNNN-<slug>.md`) and it owns no line token. The probe is the field
//! line, `- **Name:**`: the record's grammar, so a field-shaped line outside the ledger shows up
//! beside the ones it judges.

use crate::shapes::table::DOCS;
use crate::shapes::{Declared, Shape, Verdict};

/// Is `rel` a record the ledger reads?
#[must_use]
pub fn is_record(rel: &str) -> bool {
    rel.strip_prefix(&format!("{}/", super::DIR))
        .is_some_and(|n| super::NAME.is_match(n))
}

/// Every line of a record, none of any other file.
///
/// # Errors
///
/// None; the signature is the table's.
pub fn judged(rel: &str, text: &str) -> Result<Vec<usize>, String> {
    Ok(if is_record(rel) {
        (1..=text.lines().count()).collect()
    } else {
        Vec::new()
    })
}

/// This scanner's row.
pub const SHAPES: Declared = Declared {
    probe: r"^- \*\*[A-Z][^*]*:\*\*",
    judged,
    owns: &[],
    sources: &[
        "crates/goh/src/decisions.rs",
        "crates/goh/src/decisions_shapes.rs",
    ],
    opt_in: Some("GOH_DECISIONS=on"),
    shapes: &[
        Shape {
            name: "a field of a record in the ledger",
            path: r"^docs/decisions/D-\d{4}-[^/]+\.md$",
            line: "",
            verdict: Verdict::Covered,
            fixture: ("docs/decisions/D-0001-a.md", "- **Ruling:** blue.\n"),
        },
        Shape {
            name: "a bold field line in any other doc",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope(
                "only docs/decisions/D-NNNN-<slug>.md is the ledger; a field-shaped line elsewhere \
                 is prose with a bold lead",
            ),
            fixture: ("docs/notes.md", "- **Ruling:** blue.\n"),
        },
    ],
};
