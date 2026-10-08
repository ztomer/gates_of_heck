//! A checkout drops its token (`goh checkout-credentials`).
//!
//! `actions/checkout` writes the job's token into `.git/config` as
//! `http.https://github.com/.extraheader` unless its step sets `persist-credentials: false`; every
//! later step of the job can read it there. `goh credential-urls` refuses a credential in git
//! config, `http.*.extraheader` included, so a consumer whose CI runs the structural gate after a
//! default checkout is red in CI and nowhere else -- monitor was, 2026-10-05..08, for exactly
//! this. This check moves the class to the commit: every step whose `uses:` is
//! `actions/checkout@...`, in a workflow or a composite action, sets `persist-credentials` to the
//! literal `false`, or carries `# persist-credentials-ok: <reason>` on the step or directly above
//! it (a job that pushes with that token, e.g. a release job tagging). `scan.rs` reads the YAML.
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes, beside
//! `goh credential-urls` (the same defect, caught where it is written instead of where it lands).
//! No path exemption, like `goh secrets`: a workflow under `.github/` is the repo's own by
//! construction, and the one escape is the reasoned per-step marker.

pub mod scan;

use std::fmt::Write as _;
use std::path::Path;

use scan::Problem;

const TAG: &str = "[checkout_credentials]";

fn describe(p: &Problem) -> String {
    match p {
        Problem::Unset => "actions/checkout without `persist-credentials` -- the default (true) \
                           leaves the job token in .git/config"
            .to_owned(),
        Problem::NotFalse(v) => format!(
            "`persist-credentials: {v}` -- only the literal `false` keeps the token out of .git/config"
        ),
        Problem::EmptyReason => format!("`{}` with no reason", scan::MARKER),
        Problem::StaleMarker => format!(
            "`{}` on a checkout that already sets `persist-credentials: false` -- it exempts \
             nothing; delete it",
            scan::MARKER
        ),
        Problem::Unparsable(e) => {
            format!("not YAML this check can read, so no checkout in it is shown safe: {e}")
        }
    }
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    let mut judged = 0usize;
    for rel in files.iter().filter(|r| scan::in_scope(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        judged += 1;
        let found = String::from_utf8(blob).map_or_else(
            |_| {
                vec![scan::Finding {
                    line: 1,
                    step: String::new(),
                    problem: Problem::Unparsable("not UTF-8".to_owned()),
                }]
            },
            |text| scan::findings(&text),
        );
        for f in found {
            n += 1;
            let who = if f.step.is_empty() {
                String::new()
            } else {
                format!("{}: ", f.step)
            };
            let _ = writeln!(
                listing,
                "    {rel}:{}: {who}{}",
                f.line,
                describe(&f.problem)
            );
        }
    }
    if n == 0 {
        let ok = format!(
            "✓ {TAG} OK — {judged} {scope} workflow/action file(s), every actions/checkout drops its token\n"
        );
        return (0, ok, String::new());
    }
    let mut err =
        format!("✗ {TAG} {n} checkout(s) that leave the job token in git config ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    actions/checkout writes the job token into .git/config (http.<url>.extraheader) unless\n    \
         the step says otherwise: every later step can read it, and `goh credential-urls` refuses\n    \
         it in CI. Fix, on the step:\n          \
         with:\n            \
         persist-credentials: false\n    \
         A job that must push with that token (a release job tagging) keeps it on purpose:\n    \
         `# persist-credentials-ok: <reason>` on the step or in the comment lines directly above it.\n",
    );
    (1, String::new(), err)
}

/// `goh checkout-credentials [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
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
    let (code, out, err) = judge(&root, &files, staged);
    print!("{out}");
    eprint!("{err}");
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let label = if staged {
        "no checkout leaves its token in git config (staged)"
    } else {
        "no checkout leaves its token in git config"
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
                &crate::step_report::ported("crates/goh/src/checkoutcreds/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
