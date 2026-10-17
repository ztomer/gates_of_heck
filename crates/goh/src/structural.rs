//! The structural pipeline -- `goh structural [--staged|--full]`, the only tier
//! since Phase N3 retired `gates/structural.sh`'s Python branch.

use crate::{
    blobs, checkoutcreds, claims, credurls, deadexec, earlypipe, gatesrc, gitutil, goh_root,
    hookindex, killname, lockver, mdlinks, prefetch, provenance, pyformat, scope, shell_lint,
    steps, steps_delegated, steps_rust, substdin, tapwrite, unreaped, vendored,
};
use goh_tui::{ERR, OK};

mod trackedignored;

/// The steps this binary carries, as `structural_steps.txt` read when it was built: one stable
/// name per line, the label each step announces (`file length` for `file length <= N`), and nothing
/// else, so `docs/map.md` counts its lines. Embedded, so a
/// binary built before a step existed cannot claim it: `gates/structural.sh` compares this list
/// with the manifest of the tree it judges and names every step the binary lacks (BACKLOG 1.4).
const STEPS: &str = include_str!("../structural_steps.txt");

/// The step names in `STEPS`: every line that is neither blank nor a `#` comment.
#[must_use]
pub fn step_names() -> Vec<&'static str> {
    STEPS
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .collect()
}

/// `goh structural --list-steps`: the carried steps, one per line.
#[must_use]
pub fn list_steps() -> i32 {
    for name in step_names() {
        println!("{name}");
    }
    0
}

/// One run's reds, and whether the pipeline stops at the first of them.
///
/// Fail-fast is the DEFAULT and stays it: the pre-commit hook wants the first red and the reader
/// wants one screen of scrollback, and every consumer's commit runs this at `--staged`. Under
/// `--keep-going` (BACKLOG 6.16) a finding is recorded and the pipeline continues, so one run says
/// everything that is wrong instead of requiring one fix, one re-run, one more fix.
///
/// A REFUSAL still stops even under `--keep-going`, and `code == 2` is how every step says it: a
/// `.gatesrc` that will not parse, an exclude filter that will not compile, a `GOH_RED_RUN` timer
/// already past. Those are not findings in the repo, they are the run being unable to judge it, and
/// continuing past one produces the worst possible output — every remaining step reporting red for
/// a single unreadable key, which reads as a repo in far worse shape than it is.
struct Tally {
    keep_going: bool,
}

impl Tally {
    const fn new(keep_going: bool) -> Self {
        Self { keep_going }
    }

    /// Record a step's outcome. `Some(code)` means the run stops here and returns that code; `None`
    /// means the pipeline may continue.
    const fn red(&self, code: i32) -> Option<i32> {
        if self.keep_going && code == 1 {
            None
        } else {
            Some(code)
        }
    }

    /// The end-of-run summary: every red by name, and the exit code for having any. Silent when
    /// there were none, because a clean keep-going run printed `{OK} all structural gates passed`
    /// already and a second verdict would contradict nothing but add noise.
    ///
    /// The count line deliberately does NOT read `<n> steps failed (<anything>)`. `step_report::fail`
    /// prints `structural: <label> failed (<how>)` and four suites read THAT line to name the
    /// failing step, so a summary in the same shape would be counted as a thirteenth red by every
    /// reader of a failure — the summary is not a finding, and it has to look like one.
    fn finish() -> i32 {
        // An associated function, not a method: the summary reads the PROCESS-WIDE red recorder,
        // not this tally. Taking `&self` and ignoring it would be a lie about where the reds come
        // from — the very confusion that would let a second reds list grow beside this one.
        let reds = crate::step_report::take_reds();
        if reds.is_empty() {
            return 0;
        }
        let count = reds.len();
        let plural = if count == 1 { "step" } else { "steps" };
        println!();
        goh_tui::eoutln!("{ERR} structural: {count} {plural} failed");
        for label in &reds {
            goh_tui::eoutln!("    ✗ {label}");
        }
        goh_tui::eoutln!(
            "  → --keep-going ran every step; the default stops at the first of these."
        );
        goh_tui::eoutln!("  → fix the first: a later step can have been reading what it found.");
        1
    }
}

/// `goh structural`: every step, fail-fast unless `--keep-going`, delegated checkers started together.
#[must_use]
pub fn run(staged: bool, full: bool, keep_going: bool) -> i32 {
    let scope = match scope::resolve(staged, full) {
        Ok(scope) => scope,
        Err(message) => {
            goh_tui::eoutln!("{ERR} {message}");
            return 2;
        }
    };
    let staged = scope == scope::Scope::Staged;
    let repo = gatesrc::config_root();
    gatesrc::adopt_into_env(&repo);
    let cfg = match gatesrc::load(&repo) {
        Ok(cfg) => cfg,
        Err(message) => {
            goh_tui::eoutln!("{ERR} structural: {message}");
            return 2;
        }
    };
    let checks = goh_root().join("checks");

    println!("\n== structural gate ==");

    // One enumeration shared by every native scanner: four separate `git
    // ls-files` spawns measured ~30 ms of the ~80 ms native total
    // (2026-09-23). The delegated steps below re-enumerate inside their
    // own processes until they are ported (Phases 2-3).
    let files = match gitutil::listed_files(&repo, staged) {
        Ok(files) => files,
        Err(message) => {
            goh_tui::eoutln!("{ERR} structural: {message}");
            return 2;
        }
    };
    // ...and at --staged one read of every staged blob, shared by every
    // native scanner (Phase 3d): it was one `git show` per file PER scanner.
    if staged {
        let _cached = blobs::prefetch_staged(&repo, &files);
    }

    // The delegated checkers start together and report in order (crate::prefetch, BACKLOG P1e);
    // `_drain` joins any still running on every return path below, the red ones included.
    let _drain = prefetch::Drain;
    let specs = prefetch::collect(|| {
        let _ = steps_delegated::step_full_only(&repo, &checks, staged);
    });
    prefetch::start(specs, &checks, &repo);

    let tally = Tally::new(keep_going);
    // One macro rather than twenty `if let Some(code) = ... { return code; }` arms, so the
    // keep-going decision lives in exactly one place and a step added later cannot be wired to
    // bypass it by copying the old shape.
    macro_rules! step {
        ($outcome:expr) => {
            if let Some(code) = $outcome {
                if let Some(code) = tally.red(code) {
                    return code;
                }
            }
        };
    }

    step!(steps::step_emoji(&repo, &files, &cfg, staged));
    step!(steps::step_markers(&repo, &files, staged));
    step!(steps::step_length(&repo, &files, &cfg, staged));
    step!(steps::step_ceiling(&repo, &files, &cfg, staged));
    step!(steps::step_corpus(&repo, &cfg, staged));
    step!(shell_steps(&repo, &files, &cfg, staged, &tally));
    step!(steps::step_secrets(&repo, &files, staged));
    step!(git_config_steps(&repo, &files, staged, &tally));
    step!(steps::step_home_paths(&repo, &files, &cfg, staged));
    // A tracked file the repo's own `.gitignore` matches; beside the home-path step because both
    // find a file in the tree that one machine's state put there.
    step!(trackedignored::step(&repo, &files, staged));
    step!(steps_rust::step(&repo, &files, &cfg, staged));
    step!(provenance::step(&repo, staged));
    step!(mdlinks::step(&cfg, staged));
    step!(lockver::step());
    step!(pyformat::step(&cfg, staged));
    step!(killname::step(&cfg, staged));
    step!(unreaped::step(&cfg, staged));
    step!(late_steps(&repo, &files, &checks, &cfg, staged, &tally));
    // Last: every scanner above recorded what it read, and this judges each against its floor
    // (full scope only), or says which scanners have none.
    step!(crate::subject::step(&cfg, staged));

    // A keep-going run that reached here had findings on the way; they are named now. A
    // fail-fast run can only reach here with none, so `finish` is 0 and the pass line stands.
    let red = Tally::finish();
    if red != 0 {
        return red;
    }

    println!();
    goh_tui::outln!("{OK} all structural gates passed");
    0
}

/// The last four steps, which are the ones that read the repo's own configuration and
/// helper files rather than its source: a documented claim, a vendored copy of a house
/// checker, the rules `GOH_REQUIRES_CALL` names, and every child process's stdin.
///
/// They are last because each is a finding about a file the repo CHOOSED to write, and a
/// source-shaped defect earlier in the pipeline is the one that has to be fixed first. The
/// group is a function so `run` stays inside the line cap rather than growing a step per
/// release forever.
///
/// `or_else` was the shape here until `--keep-going` (BACKLOG 6.16): it stops at the first red in
/// the group, so a keep-going run would have reported the group's first finding and stopped before
/// its fourth. The steps are run in order and the tally decides whether a red ends the run.
fn late_steps(
    repo: &std::path::Path,
    files: &[String],
    checks: &std::path::Path,
    cfg: &gatesrc::Gatesrc,
    staged: bool,
    tally: &Tally,
) -> Option<i32> {
    let steps = [
        claims::step_gate(cfg, staged),
        vendored::step(repo, files, staged),
        crate::requires_call::step(repo, cfg, staged),
        substdin::step(cfg, files, staged),
        steps_delegated::step_full_only(repo, checks, staged),
    ];
    first_stop(steps, tally)
}

/// The shell-source steps, in order: shell lint, then two defects shellcheck 0.11.0 exits 0 on,
/// over the same files -- a race (an early-exit pipe) and dead code (a statement after `exec`) --
/// contract #12's carried hook index read anywhere but `goh_bind_hook_index`, and a bare `python3`
/// in a hook or the gate (the repo's pinned interpreter runs through `gates/python.sh`).
fn shell_steps(
    repo: &std::path::Path,
    files: &[String],
    cfg: &gatesrc::Gatesrc,
    staged: bool,
    tally: &Tally,
) -> Option<i32> {
    let steps = [
        shell_lint::step(cfg, staged),
        earlypipe::step(repo, files, cfg, staged),
        deadexec::step(repo, files, cfg, staged),
        hookindex::step(repo, files, cfg, staged),
        crate::barepy::step(repo, files, cfg, staged),
    ];
    first_stop(steps, tally)
}

/// The first outcome that must end the run, if any. Eagerly evaluated above: an ARRAY of outcomes
/// runs every step before this sees it, which under `--keep-going` is the point and under the
/// default is invisible, because the first red returns the same code it did before. A lazily
/// chained alternative would have been shorter and would have silently reintroduced the
/// stop-at-first-red this group needed to lose.
fn first_stop<const N: usize>(outcomes: [Option<i32>; N], tally: &Tally) -> Option<i32> {
    outcomes
        .into_iter()
        .flatten()
        .find_map(|code| tally.red(code))
}

/// A credential in git config, which `step_secrets` cannot see: `.git/config` is untracked.
/// Adjacent to `step_secrets` because it is the same defect class and the same reasoning about
/// it -- one committed, one not -- so a reader comparing the steps finds them adjacent rather than
/// having to know they are related. Then the commonest way a token GETS there: `actions/checkout`
/// persists the job token as `http.*.extraheader` unless its step sets
/// `persist-credentials: false`. The first step finds it in CI, after the checkout ran; the second
/// finds it in the workflow, at the commit. Last, the token's commonest use in a release: writing
/// a Homebrew tap around the tap's own gate (`goh tap-writes`).
fn git_config_steps(
    repo: &std::path::Path,
    files: &[String],
    staged: bool,
    tally: &Tally,
) -> Option<i32> {
    let steps = [
        credurls::step(),
        checkoutcreds::step(repo, files, staged),
        tapwrite::step(repo, files, staged),
    ];
    first_stop(steps, tally)
}
