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
//! WIRING (the house pattern for a class every repo has, `vendored.rs`): the structural step
//! runs in every repo. At `--staged` it refuses a NEW finding -- one the file's HEAD blob does
//! not already carry, so the commit that adds a racy pipe can still be changed -- and at full scope it NAMES the findings without failing, because an
//! existing one is a migration for that repo, not a reason to turn every gate in the estate red
//! the day this lands. `GOH_NO_EARLY_EXIT_PIPE=1` closes the ratchet: every finding fails, at
//! both scopes. Turn it on once the tree is clean. `goh early-exit-pipe` itself is always strict.

pub mod consumer;
pub mod lex;
pub mod scan;
pub mod subst;

use std::fmt::Write as _;
use std::path::Path;

pub use scan::scan_text;

const TAG: &str = "[no_early_exit_pipe]";
const OPT_IN: &str = "GOH_NO_EARLY_EXIT_PIPE";
const SHELLS: [&str; 5] = ["sh", "bash", "zsh", "ksh", "dash"];

/// One early-exit consumer reading a pipe whose status the script reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// 1-based line of the consumer.
    pub line: usize,
    /// Which consumer form (`grep -q`, `grep -m`, `head`, `awk exit`, `sed q`).
    pub kind: &'static str,
    /// The consumer's command as written, whitespace collapsed.
    pub text: String,
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

/// Is `rel` (with these leading bytes) a shell script?
fn is_shell(rel: &str, head: &[u8]) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    if [".sh", ".sh.tmpl", ".bash", ".zsh"]
        .iter()
        .any(|x| name.ends_with(x))
    {
        return true;
    }
    let Some(line) = head.strip_prefix(b"#!") else {
        return false;
    };
    let line = line.split(|b| *b == b'\n').next().unwrap_or(line);
    let line = String::from_utf8_lossy(line);
    let mut words = line.split_whitespace();
    let interp = words.next().unwrap_or("").rsplit('/').next().unwrap_or("");
    let interp = if interp == "env" {
        words.find(|w| !w.starts_with('-')).unwrap_or("")
    } else {
        interp
    };
    SHELLS.contains(&interp)
}

/// The first bytes of a worktree file, enough for its shebang.
fn head_bytes(path: &Path) -> Vec<u8> {
    use std::io::Read as _;
    let mut buf = vec![0u8; 256];
    let n = std::fs::File::open(path)
        .and_then(|mut f| f.read(&mut buf))
        .unwrap_or(0);
    buf.truncate(n);
    buf
}

/// `(path, findings)` for every in-scope script with any, and how many scripts were read.
fn scan_files(
    root: &Path,
    files: &[String],
    exclude: Option<&crate::pathfilter::PathFilter>,
    staged: bool,
) -> (Vec<(String, Vec<Finding>)>, usize) {
    let mut out = Vec::new();
    let mut checked = 0usize;
    for rel in files {
        if exclude.is_some_and(|x| x.is_match(rel)) {
            continue;
        }
        let head = if staged {
            crate::gitutil::content_bytes(root, rel, true).unwrap_or_default()
        } else {
            head_bytes(&root.join(rel))
        };
        if !is_shell(rel, &head) {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        let Ok(text) = std::str::from_utf8(&blob) else {
            continue;
        };
        checked += 1;
        let found = scan_text(text);
        if !found.is_empty() {
            out.push((rel.clone(), found));
        }
    }
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
                fix_for(f.kind)
            );
        }
    }
    s
}

fn report(hits: &[(String, Vec<Finding>)], what: &str) -> String {
    let n: usize = hits.iter().map(|h| h.1.len()).sum();
    let mut s = format!("✗ {TAG} {n} early-exit consumer(s) reading a pipe ({what}):\n");
    s.push_str(&listing(hits, "    "));
    s.push_str(
        "    The consumer exits before EOF; the producer's next write dies of SIGPIPE (141) and\n    \
         pipefail (this file's, or its caller's) makes that the pipeline's status -- a false\n    \
         that depends on load. Read the\n    \
         producer to completion first (the shape after each ->), or end the pipeline with\n    \
         `|| true` where its status is not the verdict.\n",
    );
    s
}

/// The step's verdict: `(exit code, stdout text, stderr text)`.
fn judge(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    strict: bool,
) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("✗ {TAG} {m}\n")),
    };
    let (hits, checked) = scan_files(root, files, exclude.as_ref(), staged);
    let scope = if staged { "staged" } else { "tracked" };
    if hits.is_empty() {
        return (
            0,
            format!("✓ {TAG} OK — {checked} {scope} shell source(s), no early-exit pipe\n"),
            String::new(),
        );
    }
    if strict {
        return (1, String::new(), report(&hits, scope));
    }
    if staged {
        let (new, old) = split_new(root, hits);
        let note = if old > 0 {
            format!("⚠ {TAG} {old} early-exit pipe(s) in staged files predate this commit (fix them while you are there)\n")
        } else {
            String::new()
        };
        if new.is_empty() {
            return (0, String::new(), note);
        }
        return (1, String::new(), report(&new, "NEW in this commit") + &note);
    }
    let mut warn = listing(&hits, &format!("⚠ {TAG} "));
    let _ = writeln!(
        warn,
        "⚠ {TAG} named, not failed: a NEW one is refused at commit; {OPT_IN}=1 fails them all"
    );
    (0, String::new(), warn)
}

/// `goh early-exit-pipe [--staged] [--exclude RE]`: every finding fails.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        eprintln!("✗ {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            eprintln!("✗ {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = judge(&root, &files, exclude, staged, true);
    print!("{out}");
    eprint!("{err}");
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
    match judge(repo, files, &cfg.exclude, staged, strict) {
        (0, _, warn) => {
            eprint!("{warn}");
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
