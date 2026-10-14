//! No early-exit consumer on a pipe under pipefail (`goh early-exit-pipe`).
//!
//! With pipefail on, `producer | grep -q PAT` is a race. `grep -q` exits at its first match and
//! closes the pipe; if the producer has more to write, its next write dies of SIGPIPE (141), and
//! pipefail makes 141 the pipeline's status -- so a match reads as a MISS. Whether the producer
//! had finished first is load: green in every test, false on a loaded host. Measured 2026-10-08:
//! `media_server`'s deploy.sh ran `docker compose ps | grep -qxF "$svc"` and read a RUNNING
//! service as absent; `git diff --cached --name-only | grep -qE` decided whether a gate layer
//! ran; and `ps | head -6` exited 141 in this repo's own `tools/quiet.sh`. The consumers are in
//! `consumer.rs`; the same race sits inside `$(producer | head -1)` under `set -e`.
//!
//! The fix reads the producer to completion first: `out="$(producer)"`, then
//! `grep -q PAT <<< "$out"`, `head -n1 <<< "$out"`, or `[ -n "$out" ]` for "any output". The
//! race corrupts only the STATUS, so a pipeline whose status nothing reads passes: one ending
//! `|| true`/`|| :`, and a substitution used as an argument rather than assigned (`subst.rs`).
//!
//! Scope: every shell SOURCE -- tracked `*.sh`, `*.sh.tmpl` (the template an installer is
//! regenerated from: leaving it out let a release put a fixed site straight back), `*.bash`,
//! `*.zsh`, and any file with a sh/bash/zsh/ksh/dash shebang. NOT gated on the file's own
//! pipefail line: a library runs under its caller's (`scan.rs`). Comments, strings and heredoc
//! bodies are not code (`lex.rs`).
//!
//! WIRING: the structural step runs in every repo, on by default (BACKLOG 6.9; it used to name
//! existing findings at full scope without failing, and Finance carried 16 that way). At
//! `--staged` it refuses a NEW finding -- one the file's HEAD blob does not already carry, so the
//! commit that adds a racy pipe can still be changed. At full scope each file's findings are
//! judged against `early_exit_pipe_seed.json`, a per-file count that may only fall
//! (`crate::seedcount`): a file over its count is red, a file under it is red until the seed is
//! lowered, and an unseeded finding is red. `goh early-exit-pipe --seed` writes the seed a repo
//! adopts with, and a pipe that is safe (its producer cannot outlive the reader) is said there.
//! `GOH_NO_EARLY_EXIT_PIPE=1` ignores the seed: every finding fails, at both scopes.
//! `goh early-exit-pipe` itself is always strict.

pub mod consumer;
pub mod lex;
pub mod producer;
pub mod scan;
pub mod subst;

use goh_tui::{ERR, OK, WARN};
use std::fmt::Write as _;
use std::path::Path;

pub use crate::shellsrc::is_shell;
pub use scan::scan_text;

const TAG: &str = "[no_early_exit_pipe]";

/// The seed's file name, at the repo root.
pub const SEED_FILE: &str = "early_exit_pipe_seed.json";

const SPEC: crate::seedcount::Spec = crate::seedcount::Spec {
    tag: TAG,
    file: SEED_FILE,
    noun: "shell source(s)",
    reseed: "goh early-exit-pipe --seed",
    clean: "no early-exit pipe beyond the seed",
    why: "early-exit consumers reading a pipe when this repo adopted the check; a count may only \
          fall (gates_of_heck BACKLOG 6.9)",
    subject: &crate::subject::EARLY_EXIT_PIPE,
};

fn site(f: &Finding) -> String {
    let shown: String = f.text.chars().take(100).collect();
    format!("{}: {shown}  ({}) -> {}", f.line, f.kind, f.fix())
}

/// The findings as the seed's judge reads them: `listed` is every shell source before the
/// exclusion, so an exclusion that removes them all is refused rather than passed.
fn scanned(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
) -> crate::seedcount::Scanned<Finding> {
    let listed = crate::shellsrc::sources(root, files, None, false).len();
    let (hits, read) = scan_files(root, files, exclude, false);
    crate::seedcount::Scanned {
        listed,
        read,
        floor: None,
        found: hits.into_iter().collect(),
        unreadable: Vec::new(),
    }
}

/// One early-exit consumer reading a pipe whose status the script reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// 1-based line of the consumer.
    pub line: usize,
    /// Which consumer form (`grep -q`, `grep -m`, `head`, `awk exit`, `sed q`).
    pub kind: &'static str,
    /// The consumer's command as written, whitespace collapsed.
    pub text: String,
    /// The producer never ends (`tail -f`, `yes`, ...): the capture fix would hang (6.22).
    pub endless: bool,
}

impl Finding {
    /// The fix this finding's report prints: a bounded wait for a producer that never ends,
    /// else the capture shape for its consumer form.
    #[must_use]
    pub fn fix(&self) -> &'static str {
        if self.endless {
            producer::BOUNDED
        } else {
            fix_for(self.kind)
        }
    }
}

/// The fix shape for one consumer form.
#[must_use]
pub fn fix_for(kind: &str) -> &'static str {
    match kind {
        "grep -q" => "out=\"$(producer)\"; grep -q PAT <<< \"$out\"",
        "grep -m" => "out=\"$(producer)\"; grep -m N PAT <<< \"$out\"",
        "head" => "out=\"$(producer)\"; head -n N <<< \"$out\"  (any output: [ -n \"$out\" ])",
        "awk exit" => "out=\"$(producer)\"; awk 'PROG' <<< \"$out\"",
        _ => "out=\"$(producer)\"; sed 'SCRIPT' <<< \"$out\"",
    }
}

/// `(path, findings)` for every in-scope script with any, and how many scripts were read.
fn scan_files(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    staged: bool,
) -> (Vec<(String, Vec<Finding>)>, usize) {
    let sources = crate::shellsrc::sources(root, files, exclude, staged);
    let checked = sources.len();
    let out = sources
        .into_iter()
        .filter_map(|(rel, text)| {
            let found = scan_text(&text);
            (!found.is_empty()).then_some((rel, found))
        })
        .collect();
    (out, checked)
}

/// The findings HEAD's blob of `rel` already carries (none for a new file).
fn at_head(root: &Path, rel: &str) -> Vec<Finding> {
    crate::gitutil::committed_bytes(root, rel)
        .and_then(|b| String::from_utf8(b).ok())
        .map_or_else(Vec::new, |t| scan_text(&t))
}

/// Split staged findings into `(new, already at HEAD)`: a multiset match on (kind, text), so a
/// moved line is not new and an edited one is.
fn split_new(
    root: &Path,
    hits: Vec<(String, Vec<Finding>)>,
) -> (Vec<(String, Vec<Finding>)>, usize) {
    let mut new = Vec::new();
    let mut old = 0usize;
    for (rel, found) in hits {
        let mut before = at_head(root, &rel);
        let mut fresh = Vec::new();
        for f in found {
            if let Some(p) = before
                .iter()
                .position(|b| b.kind == f.kind && b.text == f.text)
            {
                before.swap_remove(p);
                old += 1;
            } else {
                fresh.push(f);
            }
        }
        if !fresh.is_empty() {
            new.push((rel, fresh));
        }
    }
    (new, old)
}

fn listing(hits: &[(String, Vec<Finding>)], mark: &str) -> String {
    let mut s = String::new();
    for (rel, found) in hits {
        for f in found {
            let shown: String = f.text.chars().take(100).collect();
            let _ = writeln!(
                s,
                "{mark}{rel}:{}: {shown}  ({}) -> {}",
                f.line,
                f.kind,
                f.fix()
            );
        }
    }
    s
}

fn report(hits: &[(String, Vec<Finding>)], what: &str) -> String {
    let n: usize = hits.iter().map(|h| h.1.len()).sum();
    let mut s = format!("{ERR} {TAG} {n} early-exit consumer(s) reading a pipe ({what}):\n");
    s.push_str(&listing(hits, "    "));
    s.push_str(
        "    The consumer exits before EOF; the producer's next write dies of SIGPIPE (141) and\n    \
         pipefail (this file's, or its caller's) makes that the pipeline's status -- a false\n    \
         that depends on load. Read the\n    \
         producer to completion first (the shape after each ->), or end the pipeline with\n    \
         `|| true` where its status is not the verdict. A producer that never ends (`tail -f`,\n    \
         `yes`, a `--follow`) cannot be read to completion: wait on it with a deadline.\n",
    );
    s
}

/// The step's verdict: `(exit code, stdout text, stderr text)`. `floor` is the repo's
/// `GOH_EARLY_EXIT_PIPE_MIN_FILES`, which the seed's judge reads before any stale entry.
fn judge(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    strict: bool,
    floor: Option<usize>,
) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let (hits, checked) = scan_files(root, files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::EARLY_EXIT_PIPE, checked);
    let scope = if staged { "staged" } else { "tracked" };
    // At full scope a seed is read even over a clean tree: an entry for a file gone clean is a
    // seed to lower, not a pass.
    let seeded = !staged && !strict && root.join(SEED_FILE).exists();
    if hits.is_empty() && !seeded {
        return (
            0,
            format!("{OK} {TAG} OK — {checked} {scope} shell source(s), no early-exit pipe\n"),
            String::new(),
        );
    }
    if strict {
        return (1, String::new(), report(&hits, scope));
    }
    if staged && !hits.is_empty() {
        let (new, old) = split_new(root, hits);
        let note = if old > 0 {
            format!("{WARN} {TAG} {old} early-exit pipe(s) in staged files predate this commit (fix them while you are there)\n")
        } else {
            String::new()
        };
        if new.is_empty() {
            return (0, String::new(), note);
        }
        return (1, String::new(), report(&new, "NEW in this commit") + &note);
    }
    let seed = match crate::seedcount::load_seed(&SPEC, root) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let scan = crate::seedcount::Scanned {
        floor,
        ..scanned(root, files, exclude.as_ref())
    };
    let (code, text) = crate::seedcount::judge(&SPEC, &scan, &seed, site);
    if code == 0 {
        (0, text, String::new())
    } else {
        (code, String::new(), text)
    }
}

/// `goh early-exit-pipe --seed [--exclude RE]`: write the seed today's findings imply.
#[must_use]
pub fn write_seed(exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let (files, filter) = match (
        crate::gitutil::listed_files(&root, false),
        crate::steps::compile_exclude(exclude),
    ) {
        (Ok(f), Ok(x)) => (f, x),
        (Err(m), _) | (_, Err(m)) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let floor = match crate::gatesrc::load(&root) {
        Ok(cfg) => crate::subject::floor(&cfg, &crate::subject::EARLY_EXIT_PIPE),
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let scan = crate::seedcount::Scanned {
        floor,
        ..scanned(&root, &files, filter.as_ref())
    };
    if let Some(text) = crate::seedcount::refuse_seed(&SPEC, &scan) {
        goh_tui::eout(&text);
        return 1;
    }
    if let Err(e) = std::fs::write(
        root.join(SEED_FILE),
        crate::seedcount::seed_text(&SPEC, &scan),
    ) {
        goh_tui::eoutln!("{ERR} {TAG} cannot write {SEED_FILE}: {e}");
        return 2;
    }
    let total: usize = scan.found.values().map(Vec::len).sum();
    goh_tui::outln!(
        "{OK} {TAG} seeded {SEED_FILE}: {total} site(s) in {} file(s), of {} read",
        scan.found.len(),
        scan.read
    );
    0
}

/// `goh early-exit-pipe [--staged] [--exclude RE]`: every finding fails.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = judge(&root, &files, exclude, staged, true, None);
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// The structural step, in every repo (see the module docs for the ratchet).
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let label = if staged {
        "no early-exit pipe under pipefail (staged)"
    } else {
        "no early-exit pipe under pipefail"
    };
    let start = crate::step_report::begin(label);
    let strict = crate::gatesrc::opt_in(cfg, "GOH_NO_EARLY_EXIT_PIPE");
    let floor = crate::subject::floor(cfg, &crate::subject::EARLY_EXIT_PIPE);
    match judge(repo, files, &cfg.exclude, staged, strict, floor) {
        (0, _, warn) => {
            goh_tui::eout(&warn);
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/earlypipe/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
