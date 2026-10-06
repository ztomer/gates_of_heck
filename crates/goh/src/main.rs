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
pub mod claims;
pub mod commands;
pub mod emoji;
pub mod gatesrc;
pub mod gitutil;
pub mod homepaths;
pub mod index_view;
pub mod killname;
pub mod length;
pub mod lints;
pub mod lockver;
pub mod markers;
pub mod mdlinks;
pub mod mdtext;
pub mod noallow;
pub mod platform;
pub mod prefetch;
pub mod provenance;
pub mod pyjson;
pub mod pylex;
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
pub mod structural;
pub mod unreaped;
pub mod versrc;

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
    /// Fail on a version string that names no commit, no build date, or no
    /// build script behind them (native port of `check_version_provenance`).
    VersionProvenance {
        /// Repository root.
        #[arg(default_value = ".")]
        root: String,
        /// JSON list of paths known not to comply; may only shrink.
        #[arg(long)]
        baseline: Option<String>,
        /// Only the files the index holds (pre-commit scope).
        #[arg(long)]
        staged: bool,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
    },
    /// Fail when `Cargo.lock` disagrees with its manifests (native port of
    /// `check_lock_version`).
    LockVersion {
        /// Repository to read (default: the cwd's repo).
        #[arg(long)]
        root: Option<String>,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
    },
    /// Fail on a relative markdown link that resolves to no file or no anchor
    /// (native port of `check_md_links`).
    MdLinks {
        /// Repository to read (default: the cwd's repo).
        #[arg(long)]
        root: Option<String>,
        /// Judge the index, not the working tree.
        #[arg(long)]
        staged: bool,
        /// Regex on repo-relative paths to exempt.
        #[arg(long)]
        exclude: Option<String>,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
        /// Print the anchors and links of the markdown on stdin, as JSON.
        #[arg(long)]
        anchors: bool,
    },
    /// Re-derive every marked number in prose (native port of `check_claim_derivation`).
    ClaimDerivation {
        /// Repository to read (default: the cwd's repo).
        #[arg(long)]
        root: Option<String>,
        /// Judge the index, not the working tree.
        #[arg(long)]
        staged: bool,
        /// Regex on repo-relative paths to exempt.
        #[arg(long)]
        exclude: Option<String>,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
    },
    /// Fail on a process kill by NAME (native port of `check_no_kill_by_name`).
    KillByName {
        /// Regex on repo-relative paths to skip (`GOH_EXCLUDE`).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged blobs (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
        /// Print the code lines read from stdin for this path, as JSON.
        #[arg(long)]
        code_lines: Option<String>,
    },
    /// Fail on a test spawn no guard reaps, or whose reap sits below a line
    /// that can panic (native port of `check_no_unreaped_spawn`).
    UnreapedSpawn {
        /// Regex on repo-relative paths to skip (`GOH_EXCLUDE`).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged blobs (polices the index, like the Python checker).
        #[arg(long)]
        staged: bool,
        /// Judge stdin as one file with this extension; print JSON verdicts.
        #[arg(long)]
        verdicts: Option<String>,
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
        Commands::Structural { staged, full } => structural::run(staged, full),
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
        Commands::VersionProvenance {
            root,
            baseline,
            staged,
            json,
        } => provenance::run_command(&root, baseline.as_deref(), staged, json),
        Commands::LockVersion { root, json } => lockver::run_command(root.as_deref(), json),
        Commands::MdLinks {
            root,
            staged,
            exclude,
            json,
            anchors,
        } => mdlinks::run_command(root.as_deref(), staged, exclude.as_deref(), json, anchors),
        Commands::ClaimDerivation {
            root,
            staged,
            exclude,
            json,
        } => claims::run_command(root.as_deref(), staged, exclude.as_deref(), json),
        Commands::KillByName {
            exclude,
            staged,
            code_lines,
        } => killname::run_command(staged, &exclude, code_lines.as_deref()),
        Commands::UnreapedSpawn {
            exclude,
            staged,
            verdicts,
        } => unreaped::run_command(staged, &exclude, verdicts.as_deref()),
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
