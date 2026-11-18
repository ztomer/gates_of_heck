//! The differential: a declared scanner beside a plain grep for its token (`goh shape-diff`).
//!
//! Every probe hit is one of four things: JUDGED (the scanner's own hook read a verdict on that
//! line), named OUT OF SCOPE by a shape, inside a named GAP (an open queue item), or UNNAMED -- a
//! shape of the scanner's input nobody decided about, which is the class this module closes, and
//! red. A covered shape names nothing: a covered hit the scanner did not judge is unnamed.
//!
//! The red is judged from `crate::grace::OVER_FROM` (2026-11-23); before it the same list prints as
//! a warning.

use goh_tui::{ERR, OK, START, WARN};
use regex::Regex;
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

use super::{Declared, Row, Shape, State, Verdict};

const TAG: &str = "[shape_diff]";
/// The most unnamed hits listed per scanner; the count is always whole.
const LISTED: usize = 40;
/// A file larger than this is not read: no scanner's subject is a multi-megabyte blob.
const MAX_BYTES: usize = 2 << 20;

/// One probe hit: where, and the line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Hit {
    pub repo: String,
    pub rel: String,
    pub line: usize,
    pub text: String,
}

impl Hit {
    fn show(&self) -> String {
        let text: String = self.text.trim().chars().take(100).collect();
        if self.repo.is_empty() {
            format!("{}:{}: {text}", self.rel, self.line)
        } else {
            format!("{}: {}:{}: {text}", self.repo, self.rel, self.line)
        }
    }
}

/// What one scanner's differential found.
#[derive(Debug, Default)]
pub struct Tally {
    pub files: usize,
    pub hits: usize,
    pub judged: usize,
    /// Out-of-scope shape -> hits it named.
    pub out_of_scope: BTreeMap<&'static str, usize>,
    /// `(item, shape, hit)` for each hit inside a named gap.
    pub gaps: Vec<(&'static str, &'static str, Hit)>,
    pub unnamed: Vec<Hit>,
}

/// One declared row, compiled.
pub struct Compiled {
    pub scanner: &'static str,
    decl: &'static Declared,
    probe: Regex,
    shapes: Vec<(&'static Shape, Option<Regex>, Option<Regex>)>,
}

fn rx(p: &str, what: &str) -> Result<Option<Regex>, String> {
    if p.is_empty() {
        return Ok(None);
    }
    Regex::new(p)
        .map(Some)
        .map_err(|e| format!("shape table: {what} {p:?}: {e}"))
}

impl Compiled {
    /// # Errors
    ///
    /// A row that is not declared, or a pattern in it that does not compile.
    pub fn new(row: &'static Row) -> Result<Self, String> {
        let State::Declared(decl) = &row.state else {
            return Err(format!("`{}` has no declared shapes", row.scanner));
        };
        let probe =
            rx(decl.probe, "probe")?.ok_or_else(|| format!("`{}`: empty probe", row.scanner))?;
        let shapes = decl
            .shapes
            .iter()
            .map(|s| Ok((s, rx(s.path, s.name)?, rx(s.line, s.name)?)))
            .collect::<Result<_, String>>()?;
        Ok(Self {
            scanner: row.scanner,
            decl,
            probe,
            shapes,
        })
    }

    /// The probe's hit lines (1-based) in `text`.
    #[must_use]
    pub fn hits(&self, text: &str) -> Vec<usize> {
        text.lines()
            .enumerate()
            .filter(|(_, l)| self.probe.is_match(l))
            .map(|(i, _)| i + 1)
            .collect()
    }

    /// The shapes whose path and line patterns both match.
    pub fn matching<'a>(
        &'a self,
        rel: &'a str,
        line: &'a str,
    ) -> impl Iterator<Item = &'static Shape> + 'a {
        self.shapes
            .iter()
            .filter(move |(_, p, l)| {
                p.as_ref().is_none_or(|p| p.is_match(rel))
                    && l.as_ref().is_none_or(|l| l.is_match(line))
            })
            .map(|(s, _, _)| *s)
    }

    /// The scanner's judged lines in `rel`.
    ///
    /// # Errors
    ///
    /// The hook's own error.
    pub fn judged(&self, rel: &str, text: &str) -> Result<Vec<usize>, String> {
        (self.decl.judged)(rel, text)
    }

    /// Add one file to `tally`.
    ///
    /// # Errors
    ///
    /// The hook's own error.
    pub fn file(&self, repo: &str, rel: &str, text: &str, tally: &mut Tally) -> Result<(), String> {
        let hits = self.hits(text);
        tally.files += 1;
        if hits.is_empty() {
            return Ok(());
        }
        let judged: std::collections::BTreeSet<usize> =
            self.judged(rel, text)?.into_iter().collect();
        let lines: Vec<&str> = text.lines().collect();
        for n in hits {
            tally.hits += 1;
            if judged.contains(&n) {
                tally.judged += 1;
                continue;
            }
            let line = lines.get(n - 1).copied().unwrap_or("");
            let hit = Hit {
                repo: repo.to_owned(),
                rel: rel.to_owned(),
                line: n,
                text: line.to_owned(),
            };
            match self
                .matching(rel, line)
                .find(|s| s.verdict != Verdict::Covered)
            {
                Some(s) => match s.verdict {
                    Verdict::OutOfScope(_) => *tally.out_of_scope.entry(s.name).or_default() += 1,
                    Verdict::Gap(item) => tally.gaps.push((item, s.name, hit)),
                    Verdict::Covered => {}
                },
                None => tally.unnamed.push(hit),
            }
        }
        Ok(())
    }
}

/// The report for one scanner's tally: (red?, text).
#[must_use]
pub fn report(scanner: &str, t: &Tally, repos: usize) -> (bool, String) {
    let mut s = String::new();
    let _ = writeln!(
        s,
        "{START} {TAG} `{scanner}`: {} over {} in {}: {} judged, {} named out of scope, {} in a named gap, {} unnamed",
        goh_tui::counted(t.hits, "probe hit"),
        goh_tui::counted(t.files, "file"),
        goh_tui::counted(repos, "repo"),
        t.judged,
        t.out_of_scope.values().sum::<usize>(),
        t.gaps.len(),
        t.unnamed.len()
    );
    for (shape, n) in &t.out_of_scope {
        let _ = writeln!(s, "{START}     out of scope  {n:>5}  {shape}");
    }
    let mut by_gap: BTreeMap<(&str, &str), Vec<&Hit>> = BTreeMap::new();
    for (item, shape, hit) in &t.gaps {
        by_gap.entry((item, shape)).or_default().push(hit);
    }
    for ((item, shape), hits) in &by_gap {
        let _ = writeln!(s, "{WARN} {TAG}     gap ({item})  {}  {shape}", hits.len());
        for h in hits.iter().take(3) {
            let _ = writeln!(s, "{START}         {}", h.show());
        }
    }
    if !t.unnamed.is_empty() {
        let _ = writeln!(
            s,
            "{ERR} {TAG} `{scanner}` does not judge {} the probe found, and no shape names them:",
            goh_tui::counted(t.unnamed.len(), "line")
        );
        for h in t.unnamed.iter().take(LISTED) {
            let _ = writeln!(s, "{START}     {}", h.show());
        }
        let _ = writeln!(
            s,
            "{START}   Name each in crates/goh/src/shapes/table.rs: cover it (a fixture the scanner judges), \
             rule it out of scope with a reason, or record the gap with the queue item that closes it."
        );
    }
    (!t.unnamed.is_empty(), s)
}

/// The estate: every registered repo still on disk (`lib/estate_registry.py`'s `installed_repos`,
/// beside the audit state `GOH_ESTATE_STATE` names). `None` when no registry exists.
#[must_use]
pub fn estate() -> Option<Vec<PathBuf>> {
    let state = std::env::var_os("GOH_ESTATE_STATE")
        .map(PathBuf::from)
        .or_else(|| {
            let base = std::env::var_os("XDG_STATE_HOME")
                .map(PathBuf::from)
                .or_else(|| std::env::var_os("HOME").map(|h| Path::new(&h).join(".local/state")))?;
            Some(base.join("goh/estate_audit.json"))
        })?;
    let text = std::fs::read_to_string(state.parent()?.join("installed_repos")).ok()?;
    Some(
        text.lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(PathBuf::from)
            .filter(|p| p.join(".git").is_dir())
            .collect(),
    )
}

/// The repository's common git directory: one per repository, whichever worktree `root` is.
fn common_dir(root: &Path) -> Option<PathBuf> {
    crate::gitroot::common_git_dir(root)
}

/// Keep the first root of each repository: a worktree named as a ROOT and its main checkout in
/// the estate are one repository, read once (the worktree's tree, the one being landed).
pub fn dedup_repos(roots: &mut Vec<PathBuf>) {
    let mut seen = std::collections::BTreeSet::new();
    roots.retain(|r| seen.insert(common_dir(r).unwrap_or_else(|| r.clone())));
}

/// The paths `git diff --name-only REV...HEAD` names in the repository at `root`.
///
/// # Errors
///
/// A revision git cannot resolve, said in words: never an empty diff.
pub fn changed_since(root: &Path, rev: &str) -> Result<Vec<String>, String> {
    let out = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["diff", "--name-only", &format!("{rev}...HEAD")])
        .env("GIT_OPTIONAL_LOCKS", "0")
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("git diff: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "`git diff {rev}...HEAD` failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(str::to_owned)
        .collect())
}

/// The declared rows a change touches: every one when the table itself changed, else each row a
/// changed path lies under.
#[must_use]
pub fn touched(changed: &[String]) -> Vec<&'static Row> {
    let table = changed
        .iter()
        .any(|c| c.starts_with("crates/goh/src/shapes/"));
    super::table::ROWS
        .iter()
        .filter(|r| match &r.state {
            State::Declared(d) => {
                table
                    || changed
                        .iter()
                        .any(|c| d.sources.iter().any(|s| c.starts_with(s)))
            }
            _ => false,
        })
        .collect()
}

/// Run `rows` over `roots`: (exit code, text). Red only when `over` (the grace has ended).
///
/// # Errors
///
/// A pattern that does not compile, a repo git cannot list, or a hook's error.
pub fn run(rows: &[&'static Row], roots: &[PathBuf], over: bool) -> Result<(i32, String), String> {
    let compiled = rows
        .iter()
        .map(|r| Compiled::new(r))
        .collect::<Result<Vec<_>, _>>()?;
    let mut tallies: Vec<Tally> = compiled.iter().map(|_| Tally::default()).collect();
    for root in roots {
        let name = root
            .file_name()
            .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
        let files = crate::gitutil::listed_files(root, false)
            .map_err(|e| format!("{}: {e}", root.display()))?;
        for rel in &files {
            let Ok(bytes) = std::fs::read(root.join(rel)) else {
                continue;
            };
            if bytes.len() > MAX_BYTES {
                continue;
            }
            let Ok(text) = String::from_utf8(bytes) else {
                continue;
            };
            for (c, t) in compiled.iter().zip(tallies.iter_mut()) {
                c.file(&name, rel, &text, t)?;
            }
        }
    }
    let mut out = String::new();
    let mut red = false;
    for (c, t) in compiled.iter().zip(&tallies) {
        let (r, text) = report(c.scanner, t, roots.len());
        red |= r;
        out.push_str(&text);
    }
    let burn = super::table::BURN_DOWN.len();
    let _ = writeln!(
        out,
        "{WARN} {TAG} {} still have no shape table (unreviewed, crates/goh/src/shapes/table.rs BURN_DOWN)",
        goh_tui::counted(burn, "scanner")
    );
    if red && !over {
        let _ = writeln!(out, "{WARN} {TAG} unnamed shapes are judged from 2026-11-23 (crate::grace); warning until then");
        return Ok((0, out));
    }
    if !red {
        let _ = writeln!(out, "{OK} {TAG} OK -- every probe hit is judged or named");
    }
    Ok((i32::from(red), out))
}

/// `goh shape-diff [ROOT...] [--estate] [--since REV] [--scanner LABEL]`.
#[must_use]
pub fn run_command(
    roots: &[PathBuf],
    estate_too: bool,
    since: Option<&str>,
    scanner: Option<&str>,
) -> i32 {
    let mut rows: Vec<&'static Row> = super::table::ROWS
        .iter()
        .filter(|r| matches!(r.state, State::Declared(_)))
        .filter(|r| scanner.is_none_or(|s| r.scanner == s))
        .collect();
    if let Some(s) = scanner.filter(|_| rows.is_empty()) {
        goh_tui::eoutln!("{ERR} {TAG} no declared row is named `{s}`");
        return 2;
    }
    let mut all: Vec<PathBuf> = roots.to_vec();
    if estate_too {
        match estate() {
            Some(e) => all.extend(e),
            None if roots.is_empty() => {
                goh_tui::outln!("{WARN} {TAG} UNSCOPED: no estate registry on this machine (installed_repos); nothing was differed");
                return 0;
            }
            None => {}
        }
    }
    if let Some(rev) = since {
        let Some(here) = crate::gitutil::repo_root().map(PathBuf::from) else {
            goh_tui::eoutln!("{ERR} {TAG} --since needs a git repository");
            return 2;
        };
        match changed_since(&here, rev) {
            Ok(changed) => {
                let touched = touched(&changed);
                rows.retain(|r| touched.iter().any(|t| std::ptr::eq(*t, *r)));
            }
            Err(m) => {
                goh_tui::eoutln!("{ERR} {TAG} {m}");
                return 2;
            }
        }
        if rows.is_empty() {
            goh_tui::outln!(
                "{OK} {TAG} no declared scanner changed since {rev}: nothing to differ"
            );
            return 0;
        }
    }
    dedup_repos(&mut all);
    if all.is_empty() {
        goh_tui::eoutln!("{ERR} {TAG} name a ROOT or pass --estate");
        return 2;
    }
    match run(&rows, &all, crate::grace::over()) {
        Ok((code, text)) => {
            goh_tui::out(&text);
            code
        }
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            2
        }
    }
}
