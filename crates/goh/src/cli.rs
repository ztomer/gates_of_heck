//! The command line: every `goh` subcommand and its flags. Split out of
//! `main.rs`, which keeps the dispatch, at the line cap.

use std::path::PathBuf;

use clap::{Parser, Subcommand};

use crate::skills;

#[derive(Debug, Parser)]
#[command(name = "goh", about = "Static structural gates", version)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Commands,
}

/// `goh skills` flags, grouped so the runner takes one argument.
#[derive(Debug, clap::Args)]
pub struct SkillsArgs {
    /// Corpus root (default: the current directory, like the checker).
    #[arg(long, default_value = ".")]
    pub root: String,
    /// Word ceiling for new skills.
    #[arg(long, default_value_t = skills::DEFAULT_MAX_WORDS)]
    pub max_words: usize,
    /// Floor on the skill count (scope guard).
    #[arg(long, default_value_t = skills::DEFAULT_MIN_SKILLS)]
    pub min_skills: usize,
    /// Baseline path (default: alongside the root).
    #[arg(long)]
    pub baseline: Option<String>,
    /// Re-record today's oversized set; deliberate, never automatic.
    #[arg(long)]
    pub update_baseline: bool,
}

#[derive(Debug, Subcommand)]
pub enum Commands {
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
    /// Run a command under a ceiling, its process group swept on expiry or on a signal (the
    /// native `lib/bounded_run.py`).
    Step {
        /// The ceiling, in seconds (a positive integer).
        #[arg(long, default_value = "900", allow_hyphen_values = true)]
        timeout: String,
        /// Seconds between TERM and KILL when the group is swept.
        #[arg(long, default_value_t = 5)]
        grace: u64,
        /// What to call the step in messages and timings (default: the command).
        #[arg(long, default_value = "")]
        label: String,
        /// The command, after `--`.
        #[arg(last = true)]
        command: Vec<String>,
    },
    /// Run a step under a ceiling and report what it left running (the native
    /// `lib/orphan_canary.py wrap`).
    Canary {
        /// The repo, for attributing a leaked process by its path (default: cwd).
        #[arg(long)]
        repo: Option<PathBuf>,
        /// Append the step's output here instead of inheriting it.
        #[arg(long)]
        log: Option<PathBuf>,
        /// Record the surviving pids here as JSON when there are any.
        #[arg(long)]
        snapshot: Option<PathBuf>,
        /// The ceiling, in seconds (a positive integer).
        #[arg(long, default_value = "900", allow_hyphen_values = true)]
        timeout: String,
        /// Seconds between TERM and KILL when the group is swept.
        #[arg(long, default_value_t = 5)]
        grace: u64,
        /// What to call the step (default: the command).
        #[arg(long, default_value = "")]
        label: String,
        /// Name every unattributable process too (accepted for the Python's CLI; no effect).
        #[arg(long)]
        verbose: bool,
        /// The command, after `--`.
        #[arg(last = true)]
        command: Vec<String>,
    },
    /// A fix or perf commit names its Class and Siblings; a third instance ends the class
    /// (`gates/commit_msg.sh`, `gates/push_gate.sh`; opt-in by `GOH_COMMIT_CLASS`).
    CommitClass {
        /// The message file git hands a commit-msg hook.
        file: Option<String>,
        /// Check every commit `git log <REV>...` names instead (the pushed range).
        #[arg(long, num_args = 1.., allow_hyphen_values = true, conflicts_with = "file")]
        range: Vec<String>,
        /// Report what the rule refuses, exit 0 (a replay of history).
        #[arg(long)]
        report: bool,
    },
    /// A Python file that calls X must also call Y, from the AST (opt-in by `GOH_REQUIRES_CALL`).
    RequiresCall {
        /// The rules file (default: `GOH_REQUIRES_CALL` in `.gatesrc`).
        #[arg(long)]
        rules: Option<String>,
        /// The repository root (default: `.`).
        root: Option<String>,
    },
    /// The proven-step cache's key, lookup and record, in-process (`gates/_proven.sh` delegates).
    Proven {
        #[command(subcommand)]
        action: ProvenAction,
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
    /// Fail on a shell file that does not parse or that shellcheck errors on
    /// (native port of `check_shell_lint.sh`).
    ShellLint {
        /// Regex on repo-relative paths to skip (`GOH_EXCLUDE`).
        #[arg(long, default_value = "")]
        exclude: String,
        /// The staged blobs, not the worktree.
        #[arg(long)]
        staged: bool,
    },
    /// Fail when the repo's Python is not ruff-formatted (native port of
    /// `check_python_formatted`).
    PythonFormatted {
        /// Trees to check (default: the repo).
        trees: Vec<String>,
        /// Judge the staged `.py` files' index blobs.
        #[arg(long)]
        staged: bool,
        /// Prove the gate: a mis-formatted file is refused and a formatted one passes.
        #[arg(long)]
        selftest: bool,
    },
    /// Fail on a credential in `.git/config` (native port of `check_no_credential_urls`).
    CredentialUrls {
        /// Repo to judge (default: the enclosing one).
        #[arg(long)]
        root: Option<String>,
        /// Findings as JSON on stdout.
        #[arg(long)]
        json: bool,
        /// Judge one URL and print `[kind, host, fingerprint]` or `null`.
        #[arg(long)]
        verdict: Option<String>,
    },
    /// Dependency currency: a pin below the graph fails; crates.io drift reports
    /// (native port of `check_dep_currency`).
    Deps {
        /// Repository to read (default: cwd).
        #[arg(long)]
        root: Option<String>,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
        /// Skip the network arm, and say so.
        #[arg(long)]
        offline: bool,
        /// Also fail on a major behind.
        #[arg(long)]
        strict: bool,
        /// Fail on any major not listed in this file.
        #[arg(long)]
        ratchet: Option<String>,
    },
    /// Fail on an emptiness assertion clippy refuses, or is silent on with a
    /// message (native port of `check_no_empty_assert`).
    EmptyAssert {
        /// Regex on repo-relative paths to skip (`GOH_EXCLUDE`).
        #[arg(long, default_value = "")]
        exclude: String,
        /// The staged blobs, not the worktree.
        #[arg(long)]
        staged: bool,
    },
    /// Fail on a pushed release tag its own commit does not declare (native
    /// port of `check_tag_version`).
    TagVersion {
        /// Repository to read (default: the cwd's repo).
        #[arg(long)]
        root: Option<String>,
        /// File of pre-push ref lines (`-` or absent: stdin).
        #[arg(long)]
        refs_file: Option<String>,
        /// Machine-readable output.
        #[arg(long)]
        json: bool,
        /// Print what one version-source strategy reads from stdin, as JSON.
        #[arg(long)]
        extract: Option<String>,
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
        /// Print the claims stdin makes, read as this path, as JSON (the grammar's test seam).
        #[arg(long, value_name = "PATH")]
        parse_claims: Option<String>,
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
    /// Fail on an early-exit consumer (`grep -q`, `grep -m`, `head`, awk `exit`, sed `q`) reading
    /// a pipe in a script that turns pipefail on: the producer's SIGPIPE becomes the verdict.
    EarlyExitPipe {
        /// Regex on repo-relative paths to skip (`GOH_EXCLUDE`).
        #[arg(long, default_value = "")]
        exclude: String,
        /// Staged blobs (polices the index).
        #[arg(long)]
        staged: bool,
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
    Lints {
        /// Prove the check can go red, and stays green on a clean tree.
        #[arg(long)]
        self_test: bool,
    },
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

/// `goh proven` actions (gates/_proven.sh's hot path).
#[derive(Debug, Subcommand)]
pub enum ProvenAction {
    /// `KEY TREE` for STEP; with ENTRIES, the scoped key. The identity's live half is on stdin.
    Key { step: String, entries: Vec<String> },
    /// `AGE LABEL` when a record of KEY, made for STEP, is younger than TTL seconds.
    Lookup { key: String, step: String, ttl: u64 },
    /// Record KEY for STEP (its TREE, by LABEL), then prune what is older than TTL seconds.
    Record {
        key: String,
        tree: String,
        step: String,
        label: String,
        ttl: u64,
    },
}
