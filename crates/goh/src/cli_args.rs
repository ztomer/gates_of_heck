//! The argument types `goh` subcommands take as a group.
//!
//! `goh skills`'s and `goh tag-version`'s flags, the lone `--staged` of the no-exemption checks,
//! and `goh proven`'s actions. Split out of `cli.rs`, which holds the subcommand list, at the line
//! cap.

use clap::Subcommand;

use crate::skills;

/// The one flag of a check that polices commits and takes no path exemption.
#[derive(Debug, clap::Args)]
pub struct StagedArgs {
    /// Staged blobs (polices the index).
    #[arg(long)]
    pub staged: bool,
}

/// `goh private-names` flags.
#[derive(Debug, clap::Args)]
pub struct PrivateNamesArgs {
    /// Staged blobs (polices the index).
    #[arg(long)]
    pub staged: bool,
    /// Add NAME to the private store's list, refused (nothing written) while the work tree or
    /// `origin/HEAD` still names it (GOH.PRIVLISTORDER).
    #[arg(long, value_name = "NAME", conflicts_with = "staged")]
    pub add: Vec<String>,
}

/// A check that polices commits and takes the repo's path exemption.
#[derive(Debug, clap::Args)]
pub struct ExcludeStagedArgs {
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Staged blobs (polices the index).
    #[arg(long)]
    pub staged: bool,
}

/// `goh plural-hedge` flags: the shared exclude/staged pair, and `--all` (GOH.HEDGELIST).
#[derive(clap::Args, Debug)]
pub struct PluralHedgeArgs {
    #[command(flatten)]
    pub scope: ExcludeStagedArgs,
    /// List every site of a seeded file, not the first five and a count.
    #[arg(long)]
    pub all: bool,
}

/// `goh skills` flags, grouped so the runner takes one argument.
#[derive(Debug, clap::Args)]
pub struct SkillsArgs {
    /// Corpus root, as a path (`goh skills ~/.claude/skills`); the same as `--root`.
    #[arg(value_name = "PATH", conflicts_with = "root")]
    pub path: Option<String>,
    /// Corpus root (default: the current directory, like the checker).
    #[arg(long, default_value = ".")]
    pub root: String,
    /// Word ceiling for new skills. Unset: the repo's own `.gatesrc` `GOH_SKILLS_MAX_WORDS`, as
    /// its structural gate applies it, else the built-in ceiling.
    #[arg(long)]
    pub max_words: Option<usize>,
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

/// `goh tag-version` flags, grouped so the runner takes one argument.
#[derive(Debug, clap::Args)]
pub struct TagVersionArgs {
    /// Repository to read (default: the cwd's repo).
    #[arg(long)]
    pub root: Option<String>,
    /// File of pre-push ref lines (`-` or absent: stdin).
    #[arg(long)]
    pub refs_file: Option<String>,
    /// The remote being pushed to (the hook's `$1`): a local release tag the
    /// pushed branches carry must be in the push or already there.
    #[arg(long)]
    pub remote: Option<String>,
    /// Where to ask that remote which tags it has (the hook's `$2`; default: the name).
    #[arg(long)]
    pub remote_url: Option<String>,
    /// Machine-readable output.
    #[arg(long)]
    pub json: bool,
    /// Print what one version-source strategy reads from stdin, as JSON.
    #[arg(long)]
    pub extract: Option<String>,
}

/// `goh proven` actions (gates/_proven.sh's hot path).
#[derive(Debug, Subcommand)]
pub enum ProvenAction {
    /// `KEY TREE` for STEP; with ENTRIES, the scoped key. The identity's live half is on stdin.
    Key { step: String, entries: Vec<String> },
    /// `AGE LABEL` when a record of KEY, made for STEP, is younger than TTL seconds.
    Lookup { key: String, step: String, ttl: u64 },
    /// Record KEY for STEP (its TREE, by LABEL), then prune what is older than TTL seconds.
    /// The ARTIFACTS the step declares, as `KIND:PATH` (KIND is `produces` or `consumes`): a
    /// declared artifact that is not there is exit 2, and a record names the ones that are, so a
    /// hit is only a hit while they are still there, unchanged.
    Record {
        key: String,
        tree: String,
        step: String,
        label: String,
        ttl: u64,
        artifacts: Vec<String>,
    },
}

/// `goh roadmap`: the one roadmap's rules, against its shrink-only seed.
#[derive(Debug, clap::Args)]
pub struct RoadmapArgs {
    /// Judge the index (the tree a commit would produce).
    #[arg(long)]
    pub staged: bool,
    /// Write today's findings as `roadmap_seed.json` (refused if any count would rise).
    #[arg(long, conflicts_with = "staged")]
    pub seed: bool,
    /// The umbrella queue file (default: `GOH_ROADMAP_QUEUE`, else `GOH_LOOP_QUEUE`, else the config's).
    #[arg(long)]
    pub queue: Option<String>,
}

/// `goh deps` flags.
#[derive(Debug, clap::Args)]
pub struct DepsArgs {
    /// Repository to read (default: cwd).
    #[arg(long)]
    pub root: Option<String>,
    /// Machine-readable output.
    #[arg(long)]
    pub json: bool,
    /// Skip the network arm, and say so.
    #[arg(long)]
    pub offline: bool,
    /// Also fail on a major behind.
    #[arg(long)]
    pub strict: bool,
    /// Fail on any major not listed in this file.
    #[arg(long)]
    pub ratchet: Option<String>,
}

/// `goh crates-behind` flags.
#[derive(Debug, clap::Args)]
pub struct CratesBehindArgs {
    /// Repository to judge (default: the enclosing one).
    #[arg(long)]
    pub root: Option<String>,
    /// A `Cargo.lock` to judge, repo-relative or absolute; repeatable (default: `<root>/Cargo.lock`).
    #[arg(long = "lock")]
    pub locks: Vec<String>,
    /// Judge nothing against the registry, and say so (`GOH_DEPS_OFFLINE=1`).
    #[arg(long)]
    pub offline: bool,
    /// Seconds each cargo call may take before the lock is skipped as unreachable.
    #[arg(long, default_value_t = crate::crates_behind::DEFAULT_TIMEOUT_SECS)]
    pub timeout: u64,
    /// Every behind entry is red, whoever locked it: the owner's `cargo update` sweep. Without it,
    /// only an entry the change locked is red, and the rest warn (GOH.CRATESDRIFT, D-0001).
    #[arg(long)]
    pub live: bool,
    /// The cargo to run (a test seam; the gate never passes it).
    #[arg(long, default_value = "cargo", hide = true)]
    pub cargo: String,
    /// The sparse index read for release dates (a test seam; the gate never passes it).
    #[arg(long, default_value = crate::crates_change::DEFAULT_INDEX, hide = true)]
    pub index: String,
}

/// `goh launch-app`'s flags (GOH.CLEANOPEN, `crate::launchapp`).
#[derive(Debug, clap::Args)]
pub struct LaunchAppArgs {
    /// `open -g`: do not bring the app to the front.
    #[arg(long)]
    pub background: bool,
    /// `open -n`: a new instance even if one runs.
    #[arg(long)]
    pub new: bool,
    /// `open -W`: wait until the app exits.
    #[arg(long)]
    pub wait: bool,
    /// `open -b`: the app by its bundle identifier (`com.example.App`), in place of a TARGET.
    #[arg(short = 'b', long, value_name = "ID", conflicts_with = "target")]
    pub bundle_id: Option<String>,
    /// The `.app` bundle, an app NAME (`open -a`), or anything `open` opens.
    #[arg(required_unless_present = "bundle_id")]
    pub target: Option<std::path::PathBuf>,
    /// The app's own arguments (`open --args`), after `--`.
    #[arg(last = true)]
    pub args: Vec<std::ffi::OsString>,
}

/// Shared ceiling flags for `goh step` and `goh canary`.
#[derive(Debug, clap::Args)]
pub struct StepBoundArgs {
    /// The ceiling, in seconds (a positive integer).
    #[arg(long, default_value = "900", allow_hyphen_values = true)]
    pub timeout: String,
    /// Seconds between TERM and KILL when the group is swept.
    #[arg(long, default_value_t = 5)]
    pub grace: u64,
    /// What to call the step in messages and timings (default: the command).
    #[arg(long, default_value = "")]
    pub label: String,
    /// Process-group CPU-seconds ceiling (optional; includes reaped descendants).
    #[arg(long, allow_hyphen_values = true)]
    pub cpu_timeout: Option<String>,
    /// Stalled wall-seconds ceiling when no CPU/runnable progress occurs (optional).
    #[arg(long, allow_hyphen_values = true)]
    pub stall: Option<String>,
    /// The command, after `--`.
    #[arg(last = true)]
    pub command: Vec<String>,
}

/// `goh step` flags.
#[derive(Debug, clap::Args)]
pub struct StepArgs {
    #[command(flatten)]
    pub bound: StepBoundArgs,
}

/// `goh canary` flags.
#[derive(Debug, clap::Args)]
pub struct CanaryArgs {
    /// The repo, for attributing a leaked process by its path (default: cwd).
    #[arg(long)]
    pub repo: Option<std::path::PathBuf>,
    /// Append the step's output here instead of inheriting it.
    #[arg(long)]
    pub log: Option<std::path::PathBuf>,
    /// Record the surviving pids here as JSON when there are any.
    #[arg(long)]
    pub snapshot: Option<std::path::PathBuf>,
    /// Name every unattributable process too (accepted for the Python's CLI; no effect).
    #[arg(long)]
    pub verbose: bool,
    #[command(flatten)]
    pub bound: StepBoundArgs,
}

/// `goh seed-add`: a sweep's pending entries into a private-store seed file, under the settle's
/// lock (GOH.GRACEEND): an entry the store holds is replaced and said; one bad entry writes nothing.
#[derive(clap::Args, Debug)]
pub struct SeedAddArgs {
    /// The store file, `<kind>_seed.json` (`tui_marks_seed.json`, `floor_seed.json`, ...).
    #[arg(long)]
    pub file: String,
    /// A seed file's shape (`why` + `entries`), or the bare `entries` array.
    pub input: std::path::PathBuf,
}

/// What `goh seed-settle --help` adds: how a journal is produced (GOH.TUIMARKSSETTLE).
const SEED_SETTLE_HELP: &str = "A journal is written by a seeded check run at a commit \
(`goh tui-marks --staged`, and the other seeded checks) when `GOH_SEED_PENDING` names a directory: \
it lowers or deletes the entries of the paths the commit changed and journals that there instead \
of writing the store. The commit hook sets the variable, runs every step, and runs this only if \
all passed; a refused commit's journal is dropped unapplied. By hand: `GOH_SEED_PENDING=<dir> goh \
tui-marks --staged`, then `goh seed-settle <dir>`. `tui-marks` journals within the grace \
(`GOH_GRACE`) too. The store is `GOH_SEED_DIR`, else `$XDG_STATE_HOME/goh/seeds`.";

/// `goh seed-settle`: apply a passed commit's journals.
#[derive(clap::Args, Debug)]
#[command(after_long_help = SEED_SETTLE_HELP)]
pub struct SeedSettleArgs {
    /// The pending directory the checks journalled into (`GOH_SEED_PENDING`).
    pub pending: std::path::PathBuf,
    /// The checkout the commit is made in: off the branch the store is keyed to (`origin/HEAD`,
    /// else `main`), each settle is licensed for when the change reaches it (GOH.SEEDBRANCH).
    #[arg(long)]
    pub checkout: Option<std::path::PathBuf>,
}
