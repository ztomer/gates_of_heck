//! A hosted gate runs a current goh on a clone that holds its tags (`goh goh-pins`, GOH.WFPIN).
//!
//! "The local gate is identical to CI" broke by drift twice, and no gate saw either:
//!
//! * THE PIN. A consumer's workflow checks goh out at a named release (CI.PUBLIC: never main), and
//!   nothing refused that name going stale: the DV-4 sweep (2026-11-12) found every estate workflow
//!   on goh v0.95.0 or v0.96.0, weeks behind the goh the pre-push hook runs. `goh wf-pin --bump`
//!   now moves every pin after each goh release (GOH.PINPROPAGATE); this is the gate that refuses
//!   the one it could not move. A pin is a `ref:` beside `repository: OWNER/gates_of_heck`, a
//!   `GOH_TAG:` under any `env:`, or `git clone --branch vX.Y.Z` of goh; it may trail the RUNNING
//!   goh by `scan::WINDOW` minor releases. A checkout or clone of goh at no release at all (its
//!   default branch, a branch, a commit) is refused outright. In CI the running goh IS the pin, so
//!   there this half passes by construction; it bites on the machine, where goh is current.
//! * THE CLONE. `goh changelog-tags` judges the changelog against the `v*` tags, and a shallow
//!   clone holds none, so since GOH.TAGLESSCI it prints NOT JUDGED and passes: `sys_updater` and
//!   ztools ran it that way on every push. In a repository with a changelog, a job that runs a
//!   tag-reading gate -- `structural.sh`, `local_ci.sh`, `changelog-tags`, directly or through the
//!   one tracked script or Make target its `run:` names (`tools/gate.sh --full`, `make ci`) -- must
//!   check the repository out with `fetch-depth: 0`, which fetches every tag (divoom-control
//!   DV.FETCHDEPTH 114bea5 is the model).
//!
//! SEEDED (close-the-class): the estate's findings on the day it landed go in each repository's
//! own `goh_pins_seed.json` (`crate::estate_seed`); at or under the count warns, anything else is
//! red, a stale entry is red. Done is no entry.
//!
//! WIRING: a structural step in EVERY repo, at both scopes, beside `goh workflow-pins`.

pub mod scan;
pub mod shapes;

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

use scan::Problem;

const TAG: &str = "[goh_pins]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "a workflow runs a current goh on a clone with its tags";

/// The seed of the estate's findings on the day the check landed.
pub const KIND: Kind = Kind {
    file: "goh_pins_seed.json",
    unit: "workflow finding",
    name: "goh-pins seed",
};

const FIX: &str = "    A hosted gate runs the goh the machine runs, on a clone that holds the tags it reads\n    \
     (GOH.WFPIN). Pin goh to its newest release (`goh wf-pin --bump` moves every pin; goh ship\n    \
     runs it), never a branch; and on the job's own checkout:\n          \
     with:\n            \
     fetch-depth: 0\n    \
     A repository that cannot fix it today seeds it in its own goh_pins_seed.json.\n";

fn describe(p: &Problem) -> String {
    match p {
        Problem::Unpinned(what) | Problem::Stale(what) => what.clone(),
        Problem::Shallow(job) => format!(
            "job `{job}` runs a tag-reading gate (changelog-tags) on a shallow, tagless clone: \
             it prints NOT JUDGED and passes -- check out with `fetch-depth: 0`"
        ),
        Problem::Unparsable(e) => format!("not YAML this check can read: {e}"),
    }
}

/// The running goh's release.
fn own() -> scan::Version {
    scan::version(env!("CARGO_PKG_VERSION")).unwrap_or((0, 0, 0))
}

fn is_workflow(rel: &str) -> bool {
    crate::checkoutcreds::scan::in_scope(rel)
}

/// The verdict over the repo at `root`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    judge_as(root, files, staged, own())
}

fn judge_as(
    root: &Path,
    files: &[String],
    staged: bool,
    own: scan::Version,
) -> (i32, String, String) {
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{} {TAG} {m}\n", goh_tui::ERR)),
    };
    let changelog = ["CHANGELOG.md", "docs/CHANGELOG.md"]
        .iter()
        .any(|c| root.join(c).is_file());
    let read = |rel: &str| {
        crate::gitutil::content_bytes(root, rel, staged).and_then(|b| String::from_utf8(b).ok())
    };
    let mut sites = Sites::new();
    let mut judged = 0usize;
    for rel in files.iter().filter(|r| is_workflow(r)) {
        let Some(text) = read(rel) else {
            continue;
        };
        judged += 1;
        let found: Vec<String> = scan::findings(&text, own, changelog, &read)
            .iter()
            .map(|f| format!("    {rel}:{}: {}", f.line, describe(&f.problem)))
            .collect();
        if !found.is_empty() {
            sites.insert(rel.clone(), found);
        }
    }
    let report = Report {
        tag: TAG,
        item: "GOH.WFPIN",
        what: (
            "workflow finding: a stale or missing goh pin, or a tagless clone",
            "workflow findings: stale or missing goh pins, or tagless clones",
        ),
        fix: FIX,
        ok: format!(
            "{}: goh pinned within {} releases of v{}.{}.{}, every tag-reading job on a full clone",
            goh_tui::counted(judged, "workflow file"),
            scan::WINDOW,
            own.0,
            own.1,
            own.2
        ),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &sites, &report)
}

/// `goh goh-pins [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/gohpin/mod.rs",
        judge(repo, files, staged),
    )
}

#[cfg(test)]
mod tests;
