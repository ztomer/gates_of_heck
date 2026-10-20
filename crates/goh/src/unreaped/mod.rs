//! No unreaped spawns in tests -- Rust port of the retired `checks/check_no_unreaped_spawn.py`.
//!
//! A TEST that spawns a long-running child no guard reaps leaks it, and so
//! does one whose reap is real but sits BELOW a line that can panic: a failing
//! test skips it (`media_server`, 2026-10-03: nine orphans, each holding the
//! cargo build lock, and the only symptom was a later run printing nothing).
//! The measured table that is this gate's specification lives in the
//! reference's docstring and `tests/unreaped_spawn_table*.py`;
//! `tests/test_unreaped_spawn_native_parity.py` runs every row through both.
//!
//! SCOPE: the repository's TEST SOURCES -- test directories, test file names,
//! `.bats`, and any file carrying `#[cfg(test)]`, judged per `#[cfg(test)]`
//! region -- at BOTH tiers. The tiers differ in WHERE the files are read from,
//! not in how many of them there are: `--staged` reads the INDEX (the tree the
//! commit produces), full scope reads the WORKTREE.
//!
//! WHY the whole test scope and not the files the commit brings: a guard is a
//! CROSS-FILE contract. One `tests/common/` defines it and the other test files
//! rely on it, and a scope of "the files this commit touches" could not see that
//! contract in either direction -- it reported a file that USES a shared guard
//! as an unreaped spawn (the guard was invisible), and it passed a commit that
//! BROKE a shared guard (the files that rely on it were not in scope, so the
//! tree leaked and the gate passed it). The cost is a whole-tree read at commit
//! time; see [`scope`] for the measurement.
//!
//! WHAT IT NO LONGER DOES: judge only the commit's files. A finding in a file
//! the commit does not touch is now a finding, because the leak it names is in
//! the tree the commit produces -- which is the tree the step is about.

pub mod guard;
pub mod lex;
pub mod mask;
pub mod pysh;
pub mod rust_rules;
pub mod scope;

use goh_tui::{ERR, OK, START};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

use lex::Lex;
use rust_rules::{CrateFacts, Verdict};

/// Directories whose files are tests wherever they sit.
pub const TEST_DIRS: [&str; 7] = [
    "tests",
    "benches",
    "spec",
    "__tests__",
    "test",
    "testing",
    "e2e",
];

/// Tags that are counted and shown, never failed on.
const REPORTED: [&str; 3] = ["handoff", "watchdog", "unjudgeable"];

/// The reference's 40-hit display cap.
const MAX_SHOWN: usize = 40;

/// One scan's compiled patterns plus the test-name rule.
pub struct Scanner {
    lex: Lex,
    test_name: regex::Regex,
}

impl Scanner {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            lex: Lex::new()?,
            test_name: regex::Regex::new(
                r"(?:^|[._-])(?:test|tests|spec)[._-]|_(?:test|spec)s?\.[a-z]+$",
            )
            .map_err(|e| format!("test-name pattern: {e}"))?,
        })
    }

    fn mask(&self, ext: &str, text: &str) -> Option<String> {
        match ext {
            "rs" => Some(mask::mask_rust(text)),
            "py" => Some(mask::mask_py(text)),
            "sh" | "bash" => Some(mask::mask_shell(text, &self.lex.heredoc)),
            _ => None,
        }
    }

    fn is_test_file(&self, rel: &str, masked: &str) -> bool {
        let parts: Vec<&str> = rel.split('/').collect();
        if parts[..parts.len() - 1]
            .iter()
            .any(|p| TEST_DIRS.contains(p))
        {
            return true;
        }
        let name = parts[parts.len() - 1];
        let bats = Path::new(name).extension().is_some_and(|e| e == "bats");
        if bats || self.test_name.is_match(name) {
            return true;
        }
        masked.contains("#[cfg(test)]") || masked.replace(' ', "").contains("#[cfg(test)]")
    }

    /// One file's verdicts from its masked text. `.rs` is limited to its
    /// `#[cfg(test)]` regions and reads the crate-scope guard types.
    fn analyse(&self, ext: &str, masked: &str, facts: &CrateFacts) -> Vec<Verdict> {
        match ext {
            "rs" => rust_rules::rust_findings(&self.lex, &guard::rust_region(masked), facts),
            "py" => pysh::python_findings(&self.lex, masked),
            _ => pysh::shell_findings(&self.lex, masked),
        }
    }

    /// The reference's `verdicts(text, ext)`: one file, no crate scope. The
    /// parity harness's entry point.
    #[must_use]
    pub fn verdicts(&self, text: &str, ext: &str) -> Option<Vec<Verdict>> {
        let masked = self.mask(ext, text)?;
        Some(self.analyse(ext, &masked, &CrateFacts::default()))
    }

    /// Patterns built during the scan that failed to compile.
    #[must_use]
    pub fn errors(&self) -> Vec<String> {
        self.lex.errors()
    }
}

fn ext_of(rel: &str) -> &str {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    match name.rfind('.') {
        Some(dot) if dot > 0 && name[..dot].chars().any(|c| c != '.') => &name[dot + 1..],
        _ => "",
    }
}

/// The extensions the scan reads. Everything else is not a source it can judge.
const SOURCE_EXTS: [&str; 4] = ["rs", "py", "sh", "bash"];

/// Whether this path is a source the scan can read at all.
fn is_candidate(rel: &str) -> bool {
    SOURCE_EXTS.contains(&ext_of(rel))
}

/// What a scan found.
#[derive(Default)]
pub struct Outcome {
    pub examined: usize,
    pub findings: Vec<String>,
    pub counts: BTreeMap<&'static str, usize>,
}

/// Scan every listed path that is a test source.
///
/// The listed paths are the SCOPE (`scope::files`): the whole test scope at both
/// tiers, read from the index at `--staged` and from the worktree at full scope.
/// The crate-scope facts below are derived from the SAME set, so a guard defined
/// in one test file is protection in every other one -- which is the whole point
/// of the scope, and what a scope of "the files this commit brings" could not do.
#[must_use]
pub fn scan(
    scanner: &Scanner,
    root: &Path,
    paths: &[String],
    staged: bool,
    exclude: Option<&crate::pathfilter::PathFilter>,
) -> Outcome {
    let mut sources: Vec<(&str, &str, String)> = Vec::new();
    for rel in paths {
        let ext = ext_of(rel);
        if !is_candidate(rel) || exclude.is_some_and(|x| x.is_match(rel)) {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if blob[..blob.len().min(8000)].contains(&0) {
            continue;
        }
        let text = String::from_utf8_lossy(&blob);
        let Some(masked) = scanner.mask(ext, &text) else {
            continue;
        };
        if scanner.is_test_file(rel, &masked) {
            sources.push((rel, ext, masked));
        }
    }
    let crate_text = sources
        .iter()
        .filter(|(_, ext, _)| *ext == "rs")
        .map(|(_, _, masked)| masked.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    let facts = CrateFacts::derive(&scanner.lex, &crate_text);
    let mut out = Outcome::default();
    for (rel, ext, masked) in &sources {
        out.examined += 1;
        for (n, tag, why) in scanner.analyse(ext, masked, &facts) {
            if REPORTED.contains(&tag) {
                *out.counts.entry(tag).or_insert(0) += 1;
            } else {
                out.findings.push(format!("{rel}:{n}: {tag}: {why}"));
            }
        }
    }
    out
}

/// The reference's report, line for line: (exit code, text).
#[must_use]
pub fn report(outcome: &Outcome) -> (i32, String) {
    let mut s = String::new();
    if outcome.examined == 0 {
        let _ = writeln!(
            s,
            "{OK} [no_unreaped_spawn] not applicable — this repo has no test source in scope"
        );
        let _ = writeln!(
            s,
            "{START}   looked for {}/, test_* / *_test names, *.bats, and #[cfg(test)]",
            TEST_DIRS.join(", ")
        );
        return (0, s);
    }
    if !outcome.findings.is_empty() {
        let _ = writeln!(
            s,
            "{ERR} [no_unreaped_spawn] {} unreaped spawn(s) in test sources:",
            outcome.findings.len()
        );
        for hit in outcome.findings.iter().take(MAX_SHOWN) {
            let _ = writeln!(s, "{ERR}     {hit}");
        }
        for line in [
            "The only shape that survives a panic is a GUARD: wrap the Child in a ReapOnDrop whose",
            "Drop impl calls kill() and wait(), or use `with subprocess.Popen(...)`, or a shell",
            "`trap cleanup EXIT` that kills $!. If the reap is explicit, put it ABOVE the first",
            "assertion -- the ordering IS the defect (media_server, 2026-10-03: nine live orphans,",
            "each holding the cargo build lock, and the only observable was a test run printing",
            "nothing at all). A gate with no ceiling on its own wait is the other half: see",
            "lib/bounded_run.py and GOH_LCI_TIMEOUT / GOH_STEP_TIMEOUT.",
        ] {
            let _ = writeln!(s, "{START} {line}");
        }
        return (1, s);
    }
    let tail = outcome
        .counts
        .iter()
        .map(|(tag, n)| format!("{n} {tag}"))
        .collect::<Vec<_>>()
        .join("; ");
    let _ = write!(
        s,
        "{OK} [no_unreaped_spawn] OK — {} test file(s), every spawned child reaped on a panic",
        outcome.examined
    );
    if !tail.is_empty() {
        let _ = write!(
            s,
            " ({tail}; neither is a finding, and both are counted so a gate cannot go quiet on them)"
        );
    }
    s.push('\n');
    (0, s)
}

/// `[[line, tag, why], ...]` as JSON, for the parity harness.
#[must_use]
pub fn verdicts_json(found: &[Verdict]) -> String {
    let rows: Vec<serde_json::Value> = found
        .iter()
        .map(|(n, tag, why)| serde_json::json!([n, tag, why]))
        .collect();
    serde_json::Value::Array(rows).to_string()
}

fn run_scan(staged: bool, exclude: &str) -> Result<(i32, String), (i32, String)> {
    let filter = crate::steps::compile_exclude(exclude)
        .map_err(|m| (2, format!("{ERR} [no_unreaped_spawn] {m}\n")))?;
    let scanner = Scanner::new().map_err(|m| (2, format!("{ERR} [no_unreaped_spawn] {m}\n")))?;
    let root = crate::gitutil::repo_root().map_or_else(
        || std::env::current_dir().unwrap_or_default(),
        std::path::PathBuf::from,
    );
    // The SCOPE: the whole test scope, read from the index at `--staged` and
    // from the worktree at full scope (see `scope`). The index's blobs are
    // fetched in one batch first, so the wider scope costs one process rather
    // than one per file.
    let listed = scope::files(&root, staged).map_err(|m| {
        (
            2,
            format!(
                "{ERR} [no_unreaped_spawn] {m}\n{START} A git call that FAILS is not an empty tree. Fix the index, or the gate is not running.\n"
            ),
        )
    })?;
    scope::prefetch_index_blobs(&root, &listed, staged);
    let outcome = scan(&scanner, &root, &listed, staged, filter.as_ref());
    crate::subject::record(&crate::subject::UNREAPED_SPAWN, outcome.examined);
    let errors = scanner.errors();
    if !errors.is_empty() {
        return Err((
            2,
            format!("{ERR} [no_unreaped_spawn] {}\n", errors.join("; ")),
        ));
    }
    Ok(report(&outcome))
}

/// `goh unreaped-spawn [--staged] [--exclude RE]`, or `--verdicts EXT` to
/// judge stdin as one file (the reference's `verdicts(text, ext)`).
#[must_use]
pub fn run_command(staged: bool, exclude: &str, verdicts: Option<&str>) -> i32 {
    if let Some(ext) = verdicts {
        let mut text = String::new();
        if std::io::Read::read_to_string(&mut std::io::stdin(), &mut text).is_err() {
            goh_tui::eoutln!("{ERR} [no_unreaped_spawn] stdin is not UTF-8 text");
            return 2;
        }
        let scanner = match Scanner::new() {
            Ok(s) => s,
            Err(m) => {
                goh_tui::eoutln!("{ERR} [no_unreaped_spawn] {m}");
                return 2;
            }
        };
        let Some(found) = scanner.verdicts(&text, ext.trim_start_matches('.')) else {
            goh_tui::eoutln!("{ERR} [no_unreaped_spawn] no rules for extension {ext:?}");
            return 2;
        };
        let errors = scanner.errors();
        if !errors.is_empty() {
            goh_tui::eoutln!("{ERR} [no_unreaped_spawn] {}", errors.join("; "));
            return 2;
        }
        println!("{}", verdicts_json(&found));
        return 0;
    }
    let (code, text) = run_scan(staged, exclude).unwrap_or_else(|e| e);
    goh_tui::out(&text);
    code
}

/// The structural step. The label is the delegated step's, so a log reads
/// the same whichever tier ran it.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let label = if staged {
        "no unreaped spawns in tests (staged)"
    } else {
        "no unreaped spawns in tests"
    };
    let start = crate::step_report::begin(label);
    match run_scan(staged, &cfg.exclude).unwrap_or_else(|e| e) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, text) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/unreaped/mod.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
