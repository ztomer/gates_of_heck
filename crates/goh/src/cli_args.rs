//! The argument types two `goh` subcommands take as a group: `goh skills`'s flags and `goh
//! proven`'s actions. Split out of `cli.rs`, which holds the subcommand list, at the line cap.

use clap::Subcommand;

use crate::skills;

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
