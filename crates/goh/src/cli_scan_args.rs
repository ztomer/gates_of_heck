//! The flags of the ported path checks `goh` dispatches in `ported.rs`.
//!
//! One struct per subcommand, split out of `cli.rs` (which holds the subcommand list) when it stood
//! at the line cap (GOH.CAPHEADROOM, absorbing GOH.CLIFULL).

/// `goh subprocess-stdin`'s flags.
#[derive(Debug, clap::Args)]
pub struct SubprocessStdinArgs {
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Staged blobs (the index being committed).
    #[arg(long)]
    pub staged: bool,
    /// Judge this one file from the working tree and print JSON `{path, calls, findings}`;
    /// exit 1 when it has findings. For a repo's own verify step and the test harness.
    #[arg(long)]
    pub source: Option<String>,
    /// Rewrite the allowlist from today's findings: entries still in use kept as written, new
    /// ones `unreviewed`, stale ones dropped. Refuses over uncommitted Python.
    #[arg(long, conflicts_with_all = ["staged", "source"])]
    pub seed: bool,
}

/// `goh version-provenance`'s flags.
#[derive(Debug, clap::Args)]
pub struct VersionProvenanceArgs {
    /// Repository root.
    #[arg(default_value = ".")]
    pub root: String,
    /// JSON list of paths known not to comply; may only shrink.
    #[arg(long)]
    pub baseline: Option<String>,
    /// Only the files the index holds (pre-commit scope).
    #[arg(long)]
    pub staged: bool,
    /// Machine-readable output.
    #[arg(long)]
    pub json: bool,
}

/// `goh python-formatted`'s flags.
#[derive(Debug, clap::Args)]
pub struct PythonFormattedArgs {
    /// Trees to check (default: the repo).
    pub trees: Vec<String>,
    /// Judge the staged `.py` files' index blobs.
    #[arg(long)]
    pub staged: bool,
    /// Prove the gate: a mis-formatted file is refused and a formatted one passes.
    #[arg(long)]
    pub selftest: bool,
}

/// `goh credential-urls`'s flags.
#[derive(Debug, clap::Args)]
pub struct CredentialUrlsArgs {
    /// Repo to judge (default: the enclosing one).
    #[arg(long)]
    pub root: Option<String>,
    /// Findings as JSON on stdout.
    #[arg(long)]
    pub json: bool,
    /// Judge one URL and print `[kind, host, fingerprint]` or `null`.
    #[arg(long)]
    pub verdict: Option<String>,
}

/// `goh md-links`'s flags.
#[derive(Debug, clap::Args)]
pub struct MdLinksArgs {
    /// Repository to read (default: the cwd's repo).
    #[arg(long)]
    pub root: Option<String>,
    /// Judge the index, not the working tree.
    #[arg(long)]
    pub staged: bool,
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Machine-readable output.
    #[arg(long)]
    pub json: bool,
    /// Print the anchors and links of the markdown on stdin, as JSON.
    #[arg(long)]
    pub anchors: bool,
}

/// `goh claim-derivation`'s flags.
#[derive(Debug, clap::Args)]
pub struct ClaimDerivationArgs {
    /// Repository to read (default: the cwd's repo).
    #[arg(long)]
    pub root: Option<String>,
    /// Judge the index, not the working tree.
    #[arg(long)]
    pub staged: bool,
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Machine-readable output.
    #[arg(long)]
    pub json: bool,
    /// Print the claims stdin makes, read as this path, as JSON (the grammar's test seam).
    #[arg(long, value_name = "PATH")]
    pub parse_claims: Option<String>,
}

/// `goh kill-by-name`'s flags.
#[derive(Debug, clap::Args)]
pub struct KillByNameArgs {
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Staged blobs (polices the index, like the Python checker).
    #[arg(long)]
    pub staged: bool,
    /// Print the code lines read from stdin for this path, as JSON.
    #[arg(long)]
    pub code_lines: Option<String>,
}

/// `goh early-exit-pipe`'s flags.
#[derive(Debug, clap::Args)]
pub struct EarlyExitPipeArgs {
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Staged blobs (polices the index).
    #[arg(long, conflicts_with = "seed")]
    pub staged: bool,
    /// Write today's findings as `early_exit_pipe_seed.json`, the per-file counts the
    /// structural step holds the tree to (each may only fall).
    #[arg(long)]
    pub seed: bool,
}

/// `goh unreaped-spawn`'s flags.
#[derive(Debug, clap::Args)]
pub struct UnreapedSpawnArgs {
    /// Regex on repo-relative paths to skip. Unset: the repo's own `.gatesrc` `GOH_EXCLUDE`, as
    /// its structural gate applies it; `--exclude ''` opts out.
    #[arg(long)]
    pub exclude: Option<String>,
    /// Staged blobs (polices the index, like the Python checker).
    #[arg(long)]
    pub staged: bool,
    /// Judge stdin as one file with this extension; print JSON verdicts.
    #[arg(long)]
    pub verdicts: Option<String>,
}
