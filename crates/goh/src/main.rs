//! `goh` — static structural-gate binary.
//!
//! `structural` is the whole layer-1 pipeline: native emoji / markers /
//! length / secrets scanners, the remaining checkers delegated to the Python
//! files under `$GOH_DIR/checks` (the exclusion ceiling, the skills corpus,
//! shell lint, the empty-scope and probes sweeps). `gates/structural.sh`
//! execs this binary when `install.sh` has built it to `bin/goh`, and runs
//! the Python checkers itself otherwise; `tests/test_goh_structural_parity.py`
//! pins the two to identical verdicts. `GOH_DIR` locates the checkers.

pub mod blobs;
pub mod ceiling;
pub mod commands;
pub mod emoji;
pub mod gatesrc;
pub mod gitutil;
pub mod homepaths;
pub mod index_view;
pub mod length;
pub mod lints;
pub mod markers;
pub mod noallow;
pub mod platform;
pub mod prefetch;
pub mod ratchet;
pub mod rust_depinfo;
pub mod rust_scope;
pub mod scope;
pub mod screen;
pub mod screen_mask;
pub mod screen_shapes;
pub mod secrets;
pub mod skills;
pub mod skills_audit;
pub mod step_report;
pub mod steps;
pub mod steps_delegated;

use std::path::PathBuf;

use clap::{Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(name = "goh", about = "Static structural gates", version)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

/// `goh skills` flags, grouped so the runner takes one argument.
#[derive(Debug, clap::Args)]
struct SkillsArgs {
    /// Corpus root (default: the current directory, like the checker).
    #[arg(long, default_value = ".")]
    root: String,
    /// Word ceiling for new skills.
    #[arg(long, default_value_t = skills::DEFAULT_MAX_WORDS)]
    max_words: usize,
    /// Floor on the skill count (scope guard).
    #[arg(long, default_value_t = skills::DEFAULT_MIN_SKILLS)]
    min_skills: usize,
    /// Baseline path (default: alongside the root).
    #[arg(long)]
    baseline: Option<String>,
    /// Re-record today's oversized set; deliberate, never automatic.
    #[arg(long)]
    update_baseline: bool,
}

#[derive(Debug, Subcommand)]
enum Commands {
    /// Print the git trees this binary was built from (HEAD's, or `dirty`).
    SourceTree,
    /// The input scope of a cargo workspace for the proven cache (BACKLOG P3): repo-relative
    /// paths, one per line, or `.` for the whole tree.
    RustScope {
        /// The cargo workspace directory (default: current directory).
        #[arg(default_value = ".")]
        cargo_dir: PathBuf,
        /// Instead: name every file the last build read that lies outside the scope in FILE.
        #[arg(long)]
        check_depinfo: Option<PathBuf>,
    },
    /// Run the structural gate (every repo, any language).
    Structural {
        /// Staged files only (pre-commit scope, fast).
        #[arg(long)]
        staged: bool,
        /// Every check (pre-push scope). Default when neither flag is given.
        #[arg(long)]
        full: bool,
    },
    /// Fail on merge-conflict markers (native port of `check_no_conflict_markers`).
    Markers {
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on source files over a line cap (native port of `check_file_length`).
    Length {
        /// Maximum lines per file.
        #[arg(long)]
        max: usize,
        /// Regex exempting paths (search, like the Python checker).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on disallowed emoji (native port of `check_no_emoji`).
    Emoji {
        /// Regex exempting paths (search, like the Python checker).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Extra permitted characters (spaces stripped, like the checker).
        #[arg(long, default_value = "")]
        allow: String,
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on committed secrets (native port of `check_no_secrets`).
    Secrets {
        /// Regex exempting paths (search, like the Python checker).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on hard-coded home paths (native port of `check_no_home_paths`).
    HomePaths {
        /// Regex exempting paths (search, like the Python checker).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on `#[allow]`/`#[expect]` (native port of `check_no_allow`).
    NoAllow {
        /// Regex exempting paths (search, like the Python checker).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged files only (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail on live-display calls in test targets (native port of
    /// `check_no_screen_presentation`).
    Screen {
        /// Files/dirs to scan (test targets).
        paths: Vec<String>,
        /// Glob over repo-root-relative files (e.g. `tests/*`).
        #[arg(long)]
        scope: Option<String>,
        /// Staged blobs (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
    },
    /// Fail when a crate is exempt from its workspace lint policy
    /// (native port of `check_lints_optin`).
    Lints,
    /// Audit an agent skills corpus (native port of
    /// `check_skills_corpus`).
    Skills(SkillsArgs),
    /// Compare two images within tolerances (native `lib/golden_core`
    /// metrics: no rendering, no blessing).
    Golden {
        /// Candidate image.
        a: String,
        /// Baseline image.
        b: String,
        /// JSON overrides of the defaults.
        #[arg(long, default_value = "{}")]
        tolerances: String,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
    },
    /// Enforce line-cap ceilings (native port of the structural step's
    /// `check_exclusion_has_ceiling.py` + ratchet use).
    Ceiling {
        /// File-length cap.
        #[arg(long)]
        max: Option<usize>,
        /// Paths exempt from the cap (search, like `GOH_LINE_EXCLUDE`).
        #[arg(long, default_value = "")]
        line_exclude: String,
        /// Exemptions needing no ceiling (search, like `GOH_LINE_UNBOUNDED`).
        #[arg(long, default_value = "")]
        unbounded: String,
        /// Ratchet baseline path, repo-relative (like `GOH_LINE_BASELINE`).
        #[arg(long)]
        baseline: Option<String>,
    },
}

pub(crate) fn goh_root() -> PathBuf {
    std::env::var("GOH_DIR")
        .or_else(|_| std::env::var("GOH"))
        .map_or_else(
            |_| {
                std::env::var("HOME").map_or_else(
                    |_| PathBuf::from("/Users/ztomer/Projects/gates_of_heck"),
                    |home| PathBuf::from(home).join("Projects/gates_of_heck"),
                )
            },
            PathBuf::from,
        )
}

fn main() {
    let (os, arch) = platform::current();
    if let Err(reason) = platform::check(&os, &arch) {
        eprintln!("✗ {reason}");
        std::process::exit(1);
    }
    let cli = Cli::parse();
    let code = match cli.command {
        Commands::RustScope {
            cargo_dir,
            check_depinfo,
        } => rust_scope::run_command(&cargo_dir, check_depinfo.as_deref()),
        Commands::SourceTree => {
            println!("{}", env!("GOH_SOURCE_STAMP"));
            0
        }
        Commands::Structural { staged, full } => run_structural(staged, full),
        Commands::Markers { staged } => commands::run_markers(staged),
        Commands::Length {
            max,
            exclude,
            staged,
        } => commands::run_length(max, &exclude, staged),
        Commands::Emoji {
            exclude,
            allow,
            staged,
        } => commands::run_emoji(&exclude, &allow, staged),
        Commands::Secrets { exclude, staged } => commands::run_secrets(&exclude, staged),
        Commands::HomePaths { exclude, staged } => commands::run_home_paths(&exclude, staged),
        Commands::NoAllow { exclude, staged } => commands::run_no_allow(&exclude, staged),
        Commands::Screen {
            paths,
            scope,
            staged,
        } => commands::run_screen(&paths, scope.as_deref(), staged),
        Commands::Lints => commands::run_lints(),
        Commands::Skills(args) => commands::run_skills(
            &args.root,
            args.max_words,
            args.min_skills,
            args.baseline.as_deref(),
            args.update_baseline,
        ),
        Commands::Golden {
            a,
            b,
            tolerances,
            json,
        } => commands::run_golden(&a, &b, &tolerances, json),
        Commands::Ceiling {
            max,
            line_exclude,
            unbounded,
            baseline,
        } => commands::run_ceiling(max, &line_exclude, &unbounded, baseline.as_deref()),
    };
    std::process::exit(code);
}

/// Delegate the structural gate to `gates/structural.sh`. Returns its exit code.
fn run_structural(staged: bool, full: bool) -> i32 {
    let scope = match scope::resolve(staged, full) {
        Ok(scope) => scope,
        Err(message) => {
            eprintln!("✗ {message}");
            return 2;
        }
    };
    let staged = scope == scope::Scope::Staged;
    let repo = gatesrc::config_root();
    let cfg = match gatesrc::load(&repo) {
        Ok(cfg) => cfg,
        Err(message) => {
            eprintln!("✗ structural: {message}");
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
            eprintln!("✗ structural: {message}");
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
        let _ = steps_delegated::step_shell(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_credential_urls(&repo, &checks);
        let _ = steps_delegated::step_version_provenance(&repo, &checks, staged);
        let _ = steps_delegated::step_md_links(&repo, &checks, &cfg, staged);
        let _ = steps_delegated::step_lock_version(&repo, &checks);
        let _ = steps_delegated::step_python_formatted(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_kill_by_name(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_unreaped_spawn(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_claim_derivation(&repo, &cfg, &checks, staged);
        let _ = steps_delegated::step_full_only(&repo, &checks, staged);
    });
    prefetch::start(specs, &checks, &repo);

    if let Some(code) = steps::step_emoji(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_markers(&repo, &files, staged) {
        return code;
    }
    if let Some(code) = steps::step_length(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_ceiling(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_corpus(&repo, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_shell(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps::step_secrets(&repo, &files, &cfg, staged) {
        return code;
    }
    // A credential in a git remote URL, which the step above cannot see: `.git/config` is
    // untracked. Adjacent to `step_secrets` because it is the same defect class and the same
    // reasoning about it -- one committed, one not -- so a reader comparing the two steps finds
    // them adjacent rather than having to know they are related.
    if let Some(code) = steps_delegated::step_credential_urls(&repo, &checks) {
        return code;
    }
    if let Some(code) = steps::step_home_paths(&repo, &files, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_version_provenance(&repo, &checks, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_md_links(&repo, &checks, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_lock_version(&repo, &checks) {
        return code;
    }
    if let Some(code) = steps_delegated::step_python_formatted(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_kill_by_name(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_unreaped_spawn(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_claim_derivation(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps_delegated::step_full_only(&repo, &checks, staged) {
        return code;
    }

    println!();
    println!("✓ all structural gates passed");
    0
}
