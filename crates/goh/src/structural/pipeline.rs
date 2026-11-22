//! The structural pipeline as ONE table: every step, in order, under the names
//! `structural_steps.txt` carries (GOH.SELFWALKFILTER).
//!
//! A table rather than a sequence of calls, so `--only` (`only.rs`) skips a step before it does any
//! work, and so the names `--only` accepts are the names the pipeline runs under, checked both ways
//! by `tests::every_carried_step_is_dispatched_once`. A step `structural_steps.txt` does not list
//! (the staged-only Rust policies, `GOH_INSTALLS`) carries no name and runs only unnarrowed.

use crate::{
    busyloop, checkoutcreds, claims, credurls, deadexec, earlypipe, gatesrc, hookindex, killname,
    lockver, mdlinks, movingtag, provenance, pyformat, shell_lint, steps, steps_delegated,
    steps_rust, substdin, tapwrite, unreaped, vendored, workflowpin,
};
use std::path::Path;

/// What every step reads: the one enumeration, the one config, the checkers' directory.
pub(super) struct Ctx<'a> {
    pub(super) repo: &'a Path,
    pub(super) files: &'a [String],
    pub(super) cfg: &'a gatesrc::Gatesrc,
    pub(super) checks: &'a Path,
    pub(super) staged: bool,
}

/// One step: its outcome over the context (`None` passed or did not apply, `Some(code)` red).
pub(super) type Step = fn(&Ctx<'_>) -> Option<i32>;

/// The names a step runs under: one, two for a step function that announces two, none for a step
/// the carried list does not name.
pub(super) type Names = &'static [&'static str];

/// One place in the pipeline.
pub(super) enum Entry {
    /// A step the run stops after, in fail-fast, when it is red.
    One(Names, Step),
    /// Steps run in order, ALL of them, before the first red ends the run (`super::first_stop`):
    /// `or_else` was the shape until `--keep-going` (BACKLOG 6.16), and it stopped a keep-going run
    /// at the group's first finding.
    Group(&'static [(Names, Step)]),
}

/// The delegated steps' names, ONCE: the prefetch starts their checkers under the same names the
/// sequence runs them under, so a step `--only` skips is no checker started either.
pub(super) const BUILD_OUTPUT: Names = &["no tracked build output"];
pub(super) const FRAGMENTS: Names = &["every changelog fragment is entries only"];
pub(super) const FULL_ONLY: Names = &[
    "gates refuse to pass over an empty tree",
    "gate self-proofs still pass",
];

pub(super) fn build_output(c: &Ctx<'_>) -> Option<i32> {
    steps_delegated::step_tracked_build_output(c.repo, c.checks, c.staged)
}

pub(super) fn fragments(c: &Ctx<'_>) -> Option<i32> {
    steps_delegated::step_changelog_fragments(c.repo, c.checks, c.staged)
}

pub(super) fn full_only(c: &Ctx<'_>) -> Option<i32> {
    steps_delegated::step_full_only(c.repo, c.checks, c.staged)
}

/// The shell-source steps, in order: shell lint, then two defects shellcheck 0.11.0 exits 0 on,
/// over the same files -- a race (an early-exit pipe) and dead code (a statement after `exec`) --
/// contract #12's carried hook index read anywhere but `goh_bind_hook_index`, and a bare `python3`
/// in a hook or the gate (the repo's pinned interpreter runs through `gates/python.sh`).
const SHELL: &[(Names, Step)] = &[
    (&["shell lint"], |c| shell_lint::step(c.cfg, c.staged)),
    (&["no early-exit pipe under pipefail"], |c| {
        earlypipe::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no code after exec"], |c| {
        deadexec::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no bare read of the hook's index"], |c| {
        hookindex::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no release step hides its output"], |c| {
        crate::relquiet::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no BSD-only mktemp -t"], |c| {
        crate::mktempport::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no bare open of an app"], |c| {
        crate::bareopen::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no temp, lock or stage path in a work tree"], |c| {
        crate::runpaths::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no git stopped at a deadline holding a lock"], |c| {
        crate::gitdeadline::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no test endpoint on a literal ephemeral port"], |c| {
        crate::deadport::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no hedged plural in a string"], |c| {
        crate::pluralhedge::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no hand-rolled TUI mark"], |c| {
        crate::tuimarks::step(c.repo, c.files, c.cfg, c.staged)
    }),
    (&["no bare python in a hook or gate"], |c| {
        crate::barepy::step(c.repo, c.files, c.cfg, c.staged)
    }),
];

/// A credential in git config, which `step_secrets` cannot see: `.git/config` is untracked.
/// Adjacent to `step_secrets` because it is the same defect class and the same reasoning about
/// it -- one committed, one not -- so a reader comparing the steps finds them adjacent rather than
/// having to know they are related. Then the commonest way a token GETS there: `actions/checkout`
/// persists the job token as `http.*.extraheader` unless its step sets
/// `persist-credentials: false`. The first step finds it in CI, after the checkout ran; the second
/// finds it in the workflow, at the commit. Last, the token's commonest use in a release: writing
/// a Homebrew tap around the tap's own gate (`goh tap-writes`).
const GIT_CONFIG: &[(Names, Step)] = &[
    (&["no credential in a git remote URL"], |_| credurls::step()),
    (&["no checkout leaves its token in git config"], |c| {
        checkoutcreds::step(c.repo, c.files, c.staged)
    }),
    (&["no direct write to a Homebrew tap"], |c| {
        tapwrite::step(c.repo, c.files, c.staged)
    }),
];

/// The steps that read the repo's own configuration and helper files rather than its source: a
/// documented claim, a vendored copy of a house checker, the rules `GOH_REQUIRES_CALL` names, every
/// child process's stdin, the changelog. Late, because each is a finding about a file the repo
/// CHOSE to write, and a source-shaped defect earlier in the pipeline is the one to fix first.
const LATE: &[(Names, Step)] = &[
    (&["prose claims are derived"], |c| {
        claims::step_gate(c.cfg, c.staged)
    }),
    (&["no vendored copies of house files"], |c| {
        vendored::step(c.repo, c.files, c.staged)
    }),
    (&["a file that calls X calls Y"], |c| {
        crate::requires_call::step(c.repo, c.cfg, c.staged)
    }),
    (&["every child process names its stdin"], |c| {
        substdin::step(c.cfg, c.files, c.staged)
    }),
    (&["every tag has its changelog heading"], |c| {
        crate::changelog_tags::step(c.repo, c.staged)
    }),
    (&["no changelog entry under Unreleased"], |c| {
        crate::changelog_fragments::step(c.repo, c.cfg, c.staged)
    }),
    (FRAGMENTS, fragments),
    (&["every .gatesrc key is set once"], |c| {
        crate::gatesrc_dupes::step(c.repo, c.files, c.staged)
    }),
    (&["every language linted at commit is gated at push"], |c| {
        crate::layerparity::step(c.repo, c.cfg, c.staged)
    }),
    (FULL_ONLY, full_only),
];

/// Every step, in the order the pipeline runs them.
pub(super) const PIPELINE: &[Entry] = &[
    Entry::One(&["no disallowed emoji"], |c| {
        steps::step_emoji(c.repo, c.files, c.cfg, c.staged)
    }),
    Entry::One(&["no conflict markers"], |c| {
        steps::step_markers(c.repo, c.files, c.staged)
    }),
    Entry::One(&["file length"], |c| {
        steps::step_length(c.repo, c.files, c.cfg, c.staged)
    }),
    Entry::One(
        &[
            "line-cap exemptions carry a ceiling",
            "cap-exempt files within their ceilings",
        ],
        |c| steps::step_ceiling(c.repo, c.files, c.cfg, c.staged),
    ),
    Entry::One(&["skills corpus"], |c| {
        steps::step_corpus(c.repo, c.cfg, c.staged)
    }),
    Entry::Group(SHELL),
    Entry::One(&["no committed secrets"], |c| {
        steps::step_secrets(c.repo, c.files, c.staged)
    }),
    Entry::Group(GIT_CONFIG),
    // What a build or a deploy pulls: a container image pinned to one build, never a moving tag.
    Entry::One(&["no container image on a moving tag"], |c| {
        movingtag::step(c.repo, c.files, c.staged)
    }),
    // What CI runs on and runs: a named runner image and named action releases, never a moving label.
    Entry::One(
        &["no moving runner label or action ref in a workflow"],
        |c| workflowpin::step(c.repo, c.files, c.staged),
    ),
    // Which goh CI runs, and on what clone: a current release, and every tag the gate reads.
    Entry::One(
        &["a workflow runs a current goh on a clone with its tags"],
        |c| crate::gohpin::step(c.repo, c.files, c.staged),
    ),
    // What a script RUNS: a backgrounded busy loop has nothing to reap it, and the house has
    // ONE way to make load. Beside the moving-tag step: both are the house's own tools, and
    // both are about a pin or a load that outlives the thing that started it.
    Entry::One(&["no busy loop or busy-wait in a tracked file"], |c| {
        busyloop::step(c.repo, c.files, c.staged)
    }),
    Entry::One(&["no hard-coded home paths"], |c| {
        steps::step_home_paths(c.repo, c.files, c.cfg, c.staged)
    }),
    // A tracked file the repo's own `.gitignore` matches; beside the home-path step because both
    // find a file in the tree that one machine's state put there.
    Entry::One(&["no tracked file its .gitignore ignores"], |c| {
        super::trackedignored::step(c.repo, c.files, c.staged)
    }),
    // Beside it: a tracked file that is a build's OUTPUT (owner ruling OW.29, BACKLOG 7.4). Both
    // are a file in the tree that should not be, and the same staged-scope rule applies.
    Entry::One(BUILD_OUTPUT, build_output),
    Entry::One(&[], |c| steps_rust::step(c.repo, c.files, c.cfg, c.staged)),
    // The JavaScript this repository tracks parses, when this repository asked for it
    // (`GOH_JS_SYNTAX=on`). Opt-in, because most of the estate tracks no JavaScript at all,
    // and a gate with no subject would say "not applicable" on every push forever.
    Entry::One(&["every tracked JavaScript source parses"], |c| {
        crate::jssyntax::command::step(c.cfg)
    }),
    Entry::One(&["version provenance"], |c| {
        provenance::step(c.repo, c.staged)
    }),
    Entry::One(&["markdown links resolve"], |c| {
        mdlinks::step(c.cfg, c.staged)
    }),
    // A plan that says what is installed: true the day it was written, false at the next release.
    Entry::One(&["no doc keeps the installed version by hand"], |c| {
        crate::instline::step(c.repo, c.files, c.staged)
    }),
    // What the repo says it installs: each entry in a form the installed-version reader reads.
    Entry::One(&[], |c| crate::installs::step(c.cfg)),
    // The plan itself: one file, open entries only, each with an id and a `Done when:`.
    Entry::One(&["one roadmap, open entries only"], |c| {
        crate::roadmap::step(c.repo, c.files, c.cfg, c.staged)
    }),
    Entry::One(&["Cargo.lock matches its manifests"], |_| lockver::step()),
    Entry::One(&["python is ruff-formatted"], |c| {
        pyformat::step(c.cfg, c.staged)
    }),
    Entry::One(&["no process kill by name"], |c| {
        killname::step(c.cfg, c.staged)
    }),
    Entry::One(&["no private repository named in a public tree"], |c| {
        crate::privnames::step(c.repo, c.cfg, c.staged)
    }),
    Entry::One(&["no unreaped spawns in tests"], |c| {
        unreaped::step(c.cfg, c.staged)
    }),
    Entry::Group(LATE),
    // Last: every scanner above recorded what it read, and this judges each against its floor
    // (full scope only), or says which scanners have none.
    Entry::One(&["every scanner read its declared subject"], |c| {
        crate::subject::step(c.repo, c.cfg, c.staged)
    }),
];

#[cfg(test)]
mod tests {
    use std::collections::BTreeMap;

    /// Every name the pipeline dispatches, once per place it is dispatched.
    fn dispatched() -> impl Iterator<Item = &'static str> {
        super::PIPELINE.iter().flat_map(|entry| match entry {
            super::Entry::One(names, _) => names.to_vec(),
            super::Entry::Group(steps) => steps
                .iter()
                .flat_map(|(names, _)| names.iter().copied())
                .collect(),
        })
    }

    /// Every name the pipeline dispatches is a carried step, and every carried step is dispatched
    /// exactly once: a step added to `structural_steps.txt` and not to the table (or wired twice)
    /// would be a name `--only` accepts and then runs nothing, or twice, for.
    #[test]
    fn every_carried_step_is_dispatched_once() {
        let mut seen: BTreeMap<&str, usize> = BTreeMap::new();
        for name in dispatched() {
            *seen.entry(name).or_default() += 1;
        }
        let carried: BTreeMap<&str, usize> = crate::structural::step_names()
            .into_iter()
            .map(|n| (n, 1))
            .collect();
        assert_eq!(seen, carried);
    }
}
