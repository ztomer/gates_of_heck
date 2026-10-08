//! The carried hook index is read only by `goh_bind_hook_index` (`goh bare-hook-index`).
//!
//! A commit hook carries the index being committed as `GOH_HOOK_INDEX_FILE`, beside
//! `GOH_HOOK_GIT_DIR`, the repository it belongs to (`gates/_git_env.sh`, contract #12), because
//! `commit -a` commits a temporary index only `GIT_INDEX_FILE` names. Both are exported, so every
//! process the hook starts inherits them -- including a gate that a consumer's TEST runs on a
//! fixture repository. `goh_bind_hook_index` binds the index only inside the repository it came
//! from. A script that reads the variable bare binds it anywhere: routines' and `media_server`'s
//! tools/gate.sh did `GIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-...}" git diff --cached`, and under a
//! `commit -a` a `media_server` test's fixture gate judged `media_server`'s index and died
//! "unable to read 404acec" (2026-10-08, found by the servers session).
//!
//! The rule: no shell source expands either variable (`scan.rs` says what an expansion is) except
//! the one that DEFINES `goh_bind_hook_index`. Scope: every shell source (`crate::shellsrc`).
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes. The estate sweep before it
//! found the two sites above, both fixed in their own repos; there is nothing to ratchet.

pub mod scan;

use std::fmt::Write as _;
use std::path::Path;

const TAG: &str = "[no_bare_hook_index]";

fn clip(s: &str) -> String {
    let s = s.trim();
    let mut out: String = s.chars().take(80).collect();
    if out.len() < s.len() {
        out.push_str("...");
    }
    out
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("✗ {TAG} {m}\n")),
    };
    let sources = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    for (rel, text) in &sources {
        if scan::defines_binder(text) {
            continue;
        }
        let lines: Vec<&str> = text.lines().collect();
        for r in scan::reads(text) {
            n += 1;
            let src = lines.get(r.line - 1).copied().unwrap_or("");
            let _ = writeln!(
                listing,
                "    {rel}:{}: {}  -- expands ${}",
                r.line,
                clip(src),
                r.var
            );
        }
    }
    if n == 0 {
        let ok = format!(
            "✓ {TAG} OK — {} {scope} shell source(s), the hook's index read only by goh_bind_hook_index\n",
            sources.len()
        );
        return (0, ok, String::new());
    }
    let mut err = format!(
        "✗ {TAG} {n} read(s) of the hook's carried index outside goh_bind_hook_index ({scope}):\n"
    );
    err.push_str(&listing);
    err.push_str(
        "    A hook carries the index being committed as GOH_HOOK_INDEX_FILE, beside the repository\n    \
         it belongs to (GOH_HOOK_GIT_DIR). Read bare, a gate run on ANOTHER repository -- a test's\n    \
         fixture -- binds that index there and judges the wrong tree. Bind it with\n    \
         goh_bind_hook_index (`. \"$GOH_DIR/gates/_git_env.sh\"`), which binds only inside the\n    \
         repository it came from; for one call, in a subshell:\n    \
         ( goh_bind_hook_index; git diff --cached ... ). Contract #12.\n",
    );
    (1, String::new(), err)
}

/// `goh bare-hook-index [--staged] [--exclude RE]`.
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
    let (code, out, err) = judge(&root, &files, exclude, staged);
    print!("{out}");
    eprint!("{err}");
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    let label = if staged {
        "no bare read of the hook's index (staged)"
    } else {
        "no bare read of the hook's index"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, &cfg.exclude, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/hookindex/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
