//! Every scanner declares the input shapes it covers, in ONE table (GOH.CLASS.SHAPE, 2026-11-15).
//!
//! THE CLASS: a new scanner sees only part of its input, and a scan of part reads as clean. The
//! instances: moving-tags read only Dockerfiles and YAML (`media_server`'s `linux_test.sh` ran
//! `IMAGE=rust:1` unseen), a roadmap scan counted only headings, a heredoc reader missed the second
//! heredoc, a site list named only some sites, a changelog check read only the root CHANGELOG, a
//! tool list came from `.gatesrc` alone. Each was fixed where it was found; the next scanner was
//! written the same way, because nothing asked a scanner what it does NOT read.
//!
//! THE SEAM, three parts:
//!
//!   * [`table::ROWS`]: one row per scanner in the registry (`structural_steps.txt` plus every
//!     `crate::subject::ALL` scanner). A declared row names a PROBE (a plain grep for the token
//!     the scanner judges, with none of its selection), the scanner's own JUDGED hook (the lines
//!     it reads a verdict on, through the scanner's own selection, never a copy of it), the tokens
//!     it OWNS, and its SHAPES: each covered, out of scope with a reason, or a gap that names the
//!     queue item closing it. Every shape carries a fixture, so the table is a claim the test
//!     re-proves: a covered fixture is judged, an out-of-scope or gap fixture is not (when the
//!     scanner learns it, the stale row goes red and is flipped to covered).
//!   * [`diff`]: the differential. It runs the probe and the hook side by side over trees (the
//!     estate, `goh shape-diff --estate`): a probe hit the scanner did not judge must be named by
//!     an out-of-scope or gap shape, or it is UNNAMED and red. A covered shape names nothing: a
//!     covered hit the scanner missed is the defect itself.
//!   * the walk (`tests.rs`): a scanner in the registry with no row is red, a row for no scanner is
//!     stale, and an `Unreviewed` row must be in [`table::BURN_DOWN`], today's list, which may only
//!     shrink -- so a NEW scanner cannot land without its shapes.
//!
//! OVERLAP. Two scanners judging one token count one site twice (`goh status-markers` and
//! `goh tui-marks` both judged the `\033[` CSI on a print line). Each token has ONE owner row, and
//! the walk runs every declared hook over every OTHER row's token sample: a hook that judges a
//! token it does not own is refused.

pub mod diff;
pub mod hooks;
pub mod table;

use std::collections::BTreeSet;

/// A scanner's verdict hook: the 1-based lines of `text` (the file `rel`) it judges, through its
/// own selection. `Ok(vec![])` for a file it does not read.
pub type Judged = fn(&str, &str) -> Result<Vec<usize>, String>;

/// What the table says about one input shape.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    /// The scanner judges it; its fixture's probe hits are all judged.
    Covered,
    /// Deliberately not read, and why.
    OutOfScope(&'static str),
    /// Not read yet: the queue item that closes it.
    Gap(&'static str),
}

/// One input shape: which paths and lines it is, and what the scanner does with it.
#[derive(Debug, Clone, Copy)]
pub struct Shape {
    pub name: &'static str,
    /// A regex over the repo-relative path; `""` is every path.
    pub path: &'static str,
    /// A regex over the probe-hit line; `""` is every line.
    pub line: &'static str,
    pub verdict: Verdict,
    /// `(rel, text)`: a file holding the shape, with at least one probe hit.
    pub fixture: (&'static str, &'static str),
}

/// A token class a scanner owns, with a sample holding that token and no other.
#[derive(Debug)]
pub struct Token {
    pub name: &'static str,
    pub sample: (&'static str, &'static str),
}

/// A scanner whose shapes are declared.
pub struct Declared {
    /// The plain grep for the token the scanner judges.
    pub probe: &'static str,
    pub judged: Judged,
    pub owns: &'static [Token],
    pub shapes: &'static [Shape],
    /// The paths whose change means the scanner changed (`goh shape-diff --since`).
    pub sources: &'static [&'static str],
}

/// What the table holds for one scanner.
pub enum State {
    Declared(Declared),
    /// Not yet described: on [`table::BURN_DOWN`], which only shrinks.
    Unreviewed,
    /// Reads no input of its own (it judges other gates), and why.
    NotAScanner(&'static str),
}

/// One scanner's row: its registry label and its state.
pub struct Row {
    pub scanner: &'static str,
    pub state: State,
}

/// The structural steps, one label per line: the registry the pipeline runs.
pub const STRUCTURAL_STEPS: &str = include_str!("../../structural_steps.txt");

/// Every scanner a row must exist for: each structural step and each subject's scanner.
#[must_use]
pub fn registry() -> BTreeSet<&'static str> {
    STRUCTURAL_STEPS
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .chain(crate::subject::ALL.iter().map(|s| s.scanner))
        .collect()
}

/// The row for `scanner`, if any.
#[must_use]
pub fn row(scanner: &str) -> Option<&'static Row> {
    table::ROWS.iter().find(|r| r.scanner == scanner)
}

#[cfg(test)]
mod tests;
