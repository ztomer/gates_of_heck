//! The steps this binary DELEGATES to the Python checkers.
//!
//! Split out of `steps.rs` when the line cap caught up with it, and cut at a
//! real boundary rather than an arbitrary one: every function here is a
//! `delegated()` call that spawns a checker and returns its verdict, with no
//! native scanning of its own. The steps that DO scan (emoji, markers, length,
//! ceiling, secrets, home paths, skills corpus) share a shape these do not --
//! they take the pre-read file list, and their output is a finding list -- so
//! the two clusters have no call sites in common beyond the module itself.
//!
//! Each one is mirrored in `gates/structural.sh`, which execs this binary and
//! so discards the shell; `tests/test_goh_structural.py` compares the
//! two pipelines' verdicts step for step. A step added here and not there is a
//! step that runs in exactly one of the two, which is how a gate ends up
//! present in the source and absent from every push.

use crate::step_report::delegated;

/// The label a no-tracked-build-output step announces; the same line is in `structural_steps.txt`.
const BUILD_OUTPUT_LABEL: &str = "no tracked build output";

/// `checks/check_tracked_build_output.py`: a tracked file that is a build's output (OW.29, BACKLOG
/// 7.4). Both scopes, like the tracked-ignored step beside it: the whole tree at full, and at
/// `--staged` only what the commit adds or changes, read from the index (`--staged` to the
/// checker), because a red on a file committed long ago would block every unrelated commit.
///
/// Delegated, not ported: the checker is the one implementation, with its own content sniff for an
/// extension that names both an object file and a text format (`.obj`), and the commit scope's
/// interpreter start overlaps the native scanners (`crate::prefetch`).
#[must_use]
pub fn step_tracked_build_output(
    repo: &std::path::Path,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    let py = match crate::house_python::path() {
        Ok(py) => py,
        Err(why) => {
            let start = crate::step_report::begin(BUILD_OUTPUT_LABEL);
            return Some(crate::step_report::fail(
                BUILD_OUTPUT_LABEL,
                "lib/house_python.sh",
                &why,
                start,
            ));
        }
    };
    let mut args = vec!["check_tracked_build_output.py".to_owned()];
    if staged {
        args.push("--staged".to_owned());
    }
    match delegated(checks, repo, BUILD_OUTPUT_LABEL, &py, &args) {
        0 => None,
        code => Some(code),
    }
}

#[must_use]
pub fn step_full_only(
    repo: &std::path::Path,
    checks: &std::path::Path,
    staged: bool,
) -> Option<i32> {
    // 8-9. Full scope only: empty-tree refusal and gate self-proofs.
    if staged {
        return None;
    }
    // The house interpreter (G2d), never PATH's first python3; its refusal fails the step by name.
    let py = match crate::house_python::path() {
        Ok(py) => py,
        Err(why) => {
            let label = "gates refuse to pass over an empty tree";
            let start = crate::step_report::begin(label);
            return Some(crate::step_report::fail(
                label,
                "lib/house_python.sh",
                &why,
                start,
            ));
        }
    };
    let empty_code = delegated(
        checks,
        repo,
        "gates refuse to pass over an empty tree",
        &py,
        &["check_empty_scope.py".to_owned()],
    );
    if empty_code != 0 {
        return Some(empty_code);
    }
    let probes_code = delegated(
        checks,
        repo,
        "gate self-proofs still pass",
        &py,
        &["check_probes_pass.py".to_owned()],
    );
    if probes_code != 0 {
        return Some(probes_code);
    }
    None
}
