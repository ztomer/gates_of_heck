//! A Homebrew tap is bumped through its own gate (`goh tap-writes`).
//!
//! `tools/release-kit/tap_bump.sh` clones the tap, rewrites version, urls and digests together,
//! runs the TAP's own `tools/gate.sh --full` in the clone and pushes the commit pinned; a red gate
//! pushes nothing (BACKLOG 6.8). Until 2026-10-17 three of the five producers went around it --
//! antiknob and monitor PUT the formula through the GitHub contents API, and `CadGoose`'s release
//! workflow cloned the tap with a PAT and pushed from the clone -- so the tap's gate never judged
//! their bumps, and one of them shipped stale agent urls (monitor v0.49.0). All five were moved
//! onto `tap_bump.sh` (homebrew-tap T2.1). This check keeps a sixth from coming back: a script,
//! Makefile or workflow that writes a `homebrew-*` repo's contents or pushes from a clone of one
//! is red at the commit. `scan.rs` reads the lines.
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes. No `--exclude`, like
//! `goh checkout-credentials`: the defect is a gate bypass, which is not a style a path can be
//! exempt from; the one escape is the reasoned per-line `# tap-write-ok: <reason>`.

pub mod scan;

use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

use scan::Problem;

const TAG: &str = "[tap_writes]";

fn describe(p: &Problem) -> String {
    match p {
        Problem::ApiWrite => {
            "writes a Homebrew tap's contents through the GitHub API -- the tap's \
                              gate never runs on it"
                .to_owned()
        }
        Problem::PushFromClone { clone_line } => format!(
            "pushes from the Homebrew tap cloned on line {clone_line} -- the tap's gate never runs \
             on it"
        ),
        Problem::EmptyReason => format!("`# {}` with no reason", scan::MARKER),
        Problem::StaleMarker => format!(
            "`# {}` with no tap write under it -- it exempts nothing; delete it",
            scan::MARKER
        ),
    }
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let sc = match scan::Scanner::compile() {
        Ok(sc) => sc,
        Err(e) => return (2, String::new(), format!("{ERR} {TAG} {e}\n")),
    };
    let mut listing = String::new();
    let mut n = 0usize;
    let mut judged = 0usize;
    for rel in files {
        if scan::by_name(rel) == Some(false) {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if !scan::in_scope(rel, &blob) {
            continue;
        }
        // A file that is not UTF-8 is not a script anything runs as text; there is nothing to read.
        let Ok(text) = String::from_utf8(blob) else {
            continue;
        };
        judged += 1;
        for f in scan::findings(&sc, &text) {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{}: {}", f.line, describe(&f.problem));
        }
    }
    crate::subject::record(&crate::subject::TAP_WRITES, judged);
    if n == 0 {
        let ok = format!(
            "{OK} {TAG} OK — {judged} {scope} script/workflow file(s), no direct write to a Homebrew tap\n"
        );
        return (0, ok, String::new());
    }
    let mut err = format!("{ERR} {TAG} {n} direct write(s) to a Homebrew tap ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    A bump written straight to the tap skips the tap's own gate: a wrong url or digest\n    \
         ships to every `brew upgrade`. Bump through the house step, which clones the tap, runs\n    \
         its tools/gate.sh --full and pushes only on green:\n          \
         \"$GOH_DIR/tools/release-kit/tap_bump.sh\" --tap OWNER/homebrew-NAME \\\n            \
         (--formula NAME | --cask NAME) --tag vX.Y.Z --artifact PATH|URL [--resource NAME=PATH|URL]\n    \
         A script that must write the tap itself says why: `# tap-write-ok: <reason>` on the line\n    \
         or in the comment lines directly above it.\n",
    );
    (1, String::new(), err)
}

/// `goh tap-writes [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
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
    let (code, out, err) = judge(&root, &files, staged);
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let label = if staged {
        "no direct write to a Homebrew tap (staged)"
    } else {
        "no direct write to a Homebrew tap"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/tapwrite/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
