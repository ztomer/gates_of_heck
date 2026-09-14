//! `goh` — static structural-gate binary.
//!
//! Native scanners land crate-by-crate (markers, length, emoji), keeping
//! language gates as plugin callees. `structural` still delegates to
//! `gates/structural.sh` until every scanner is native. `GOH_DIR` fallback
//! stays during migration.

pub mod commands;
pub mod emoji;
pub mod gatesrc;
pub mod gitutil;
pub mod length;
pub mod markers;
pub mod platform;
pub mod scope;
pub mod secrets;
pub mod steps;

use std::path::PathBuf;

use clap::{Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(name = "goh", about = "Static structural gates")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Debug, Subcommand)]
enum Commands {
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
}

fn goh_root() -> PathBuf {
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

    if let Some(code) = steps::step_emoji(&repo, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_markers(&repo, staged) {
        return code;
    }
    if let Some(code) = steps::step_length(&repo, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_ceiling(&repo, &cfg, &checks) {
        return code;
    }
    if let Some(code) = steps::step_corpus(&repo, &cfg, &checks) {
        return code;
    }
    if let Some(code) = steps::step_shell(&repo, &cfg, &checks, staged) {
        return code;
    }
    if let Some(code) = steps::step_secrets(&repo, &cfg, staged) {
        return code;
    }
    if let Some(code) = steps::step_full_only(&repo, &checks, staged) {
        return code;
    }

    println!();
    println!("✓ all structural gates passed");
    0
}
