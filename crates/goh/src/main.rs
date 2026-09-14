//! `goh` — static structural-gate binary.
//!
//! Phase 1 (this scaffold): strict scope parsing + platform gate + delegation
//! to the existing `gates/structural.sh`. Zero behavior change; the win is one
//! static binary at the hook entry instead of ambient `python3`/`bash`.
//!
//! Phase 2 (next): port scanners in-process crate-by-crate (emoji, markers,
//! length, secrets), keeping language gates as plugin callees. `GOH_DIR`
//! fallback stays during migration.

pub mod gitutil;
pub mod markers;
pub mod platform;
pub mod scope;

use std::path::PathBuf;
use std::process::Command;

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
    match cli.command {
        Commands::Structural { staged, full } => {
            let scope = match scope::resolve(staged, full) {
                Ok(scope) => scope,
                Err(message) => {
                    eprintln!("✗ {message}");
                    std::process::exit(2);
                }
            };
            let root = goh_root();
            let script = root.join("gates/structural.sh");
            let mut cmd = Command::new("bash");
            cmd.arg(&script);
            if scope == scope::Scope::Staged {
                cmd.arg("--staged");
            }
            let status = cmd.status().unwrap_or_else(|e| {
                eprintln!("✗ goh: failed to exec {}: {e}", script.display());
                std::process::exit(1);
            });
            std::process::exit(status.code().unwrap_or(1));
        }
        Commands::Markers { staged } => {
            let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
                eprintln!("⚠ [no_conflict_markers] not a git repo — skipping");
                return;
            };
            match markers::scan_root(&root, staged) {
                Err(message) => {
                    eprintln!("✗ [no_conflict_markers] {message}");
                    std::process::exit(2);
                }
                Ok(bad) if !bad.is_empty() => {
                    eprintln!("✗ [no_conflict_markers] merge conflict markers found:");
                    for hit in &bad {
                        eprintln!("    {}:{}: {}", hit.path, hit.line_no, hit.text);
                    }
                    std::process::exit(1);
                }
                Ok(_) => {
                    let scope = if staged { "staged" } else { "tracked" };
                    println!("→ [no_conflict_markers] OK — no markers in {scope} files");
                }
            }
        }
    }
}
