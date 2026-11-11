//! A workflow runs on a named image and named releases, never a moving label (`goh workflow-pins`).
//!
//! The house rule (2026-10-06): "latest" is a named release, never a moving tag. Two things in a
//! GitHub workflow move under an unchanged file: a runner label `ubuntu-latest` / `macos-latest` /
//! `windows-latest` (GitHub re-points it to a new OS image; CI warned `ubuntu-latest` was about to
//! become Ubuntu 26, M.FLOODREAP), and an action `uses: owner/repo@<ref>` whose ref is a branch
//! (`@stable`, `@main`) or a major-only tag (`@v4`, `@v4.1`) its owner re-points. Monitor pinned
//! its actions by hand (M.CIPARITY, v0.69.4) and nothing refused the next floating tag; this does.
//!
//! WHAT PASSES. A runner on a named image (`ubuntu-24.04`, `macos-26`); an action on a commit id
//! (an action that publishes no releases: pin the commit and say why in a comment) or a full
//! release tag (`v7.0.1`); a path in this repo (`./.github/actions/x`); and `docker://`, which
//! `goh moving-tags` judges. An expression is unreadable here, not a finding, except that a
//! `*-latest` word inside one is a finding.
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes, beside `goh moving-tags`
//! (the same class one layer up). No `--exclude`: a workflow is the repo's own by construction and
//! what CI runs on is not a style; the one escape is the reasoned per-reference
//! `# workflow-pin-ok: <reason>`. `scan.rs` reads the YAML.

pub mod scan;

use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

use scan::Problem;

const TAG: &str = "[workflow_pins]";

fn describe(p: &Problem) -> String {
    match p {
        Problem::NoRef(u) => format!("`{u}` has no `@ref`"),
        Problem::MovingAction(u, r) => format!(
            "`{u}` is on `{r}`, a ref its owner moves -- the same file runs different code on \
             different days"
        ),
        Problem::MovingRunner(l) => format!(
            "runner `{l}` -- GitHub re-points it to a new OS image; name one (`ubuntu-24.04`, \
             `macos-26`)"
        ),
        Problem::EmptyReason => format!("`# {}` with no reason", scan::MARKER),
        Problem::StaleMarker => format!(
            "`# {}` with no moving reference under it -- it exempts nothing; delete it",
            scan::MARKER
        ),
        Problem::Unparsable(e) => {
            format!("not YAML this check can read, so nothing in it is shown pinned: {e}")
        }
    }
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    let mut total = scan::Scan::default();
    let mut file_count = 0usize;
    for rel in files.iter().filter(|r| scan::in_scope(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        file_count += 1;
        let s = String::from_utf8(blob).map_or_else(
            |_| scan::Scan {
                findings: vec![scan::Finding {
                    line: 1,
                    problem: Problem::Unparsable("not UTF-8".to_owned()),
                }],
                ..scan::Scan::default()
            },
            |text| scan::scan(&text),
        );
        total.refs += s.refs;
        total.shas += s.shas;
        total.releases += s.releases;
        total.exempt += s.exempt;
        total.unreadable += s.unreadable;
        for f in &s.findings {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{}: {}", f.line, describe(&f.problem));
        }
    }
    crate::subject::record(&crate::subject::WORKFLOW_PINS, total.refs);
    if n == 0 {
        let ok = format!(
            "{OK} {TAG} OK — {scope}: {}, {}: {} pinned by commit, {} on a release tag, {} exempted \
             with a reason, {} carried by an expression it cannot resolve\n",
            goh_tui::counted(file_count, "workflow/action file"),
            goh_tui::counted(total.refs, "reference"),
            total.shas,
            total.releases,
            total.exempt,
            total.unreadable
        );
        return (0, ok, String::new());
    }
    let mut err = format!(
        "{ERR} {TAG} {}: a moving runner label or action ref in a workflow ({scope}):\n",
        goh_tui::counted(n, "finding")
    );
    err.push_str(&listing);
    err.push_str(
        "    Pin each to a named release (\"latest\" is a release, never a moving label):\n          \
         runs-on: ubuntu-24.04      # or macos-26, windows-2025; never *-latest\n          \
         uses: actions/checkout@<40-hex commit>  # v7.0.1\n    \
         (the commit of a tag: `gh api repos/OWNER/REPO/commits/TAG --jq .sha`). A full release\n    \
         tag (`@v7.0.1`) passes; `@v7`, `@v7.0` and a branch do not. An action that publishes no\n    \
         releases is pinned by commit, with a comment saying so.\n    \
         A reference that must float says why: `# workflow-pin-ok: <reason>` on the line or in the\n    \
         comment lines directly above it.\n",
    );
    (1, String::new(), err)
}

/// `goh workflow-pins [--staged]`.
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
        "no moving runner label or action ref in a workflow (staged)"
    } else {
        "no moving runner label or action ref in a workflow"
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
                &crate::step_report::ported("crates/goh/src/workflowpin/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
