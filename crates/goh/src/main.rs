//! `goh` — static structural-gate binary.
//!
//! Native scanners land crate-by-crate (markers, length, emoji), keeping
//! language gates as plugin callees. `structural` still delegates to
//! `gates/structural.sh` until every scanner is native. `GOH_DIR` fallback
//! stays during migration.

pub mod emoji;
pub mod gitutil;
pub mod length;
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
}

/// Compile an `--exclude` regex.
///
/// # Errors
///
/// Returns the regex error text when `exclude` does not compile.
fn compile_exclude(exclude: &str) -> Result<Option<regex::Regex>, String> {
    if exclude.is_empty() {
        return Ok(None);
    }
    regex::Regex::new(exclude)
        .map(Some)
        .map_err(|e| format!("bad --exclude regex: {e}"))
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
        Commands::Markers { staged } => run_markers(staged),
        Commands::Length {
            max,
            exclude,
            staged,
        } => run_length(max, &exclude, staged),
        Commands::Emoji {
            exclude,
            allow,
            staged,
        } => run_emoji(&exclude, &allow, staged),
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
    let root = goh_root();
    let script = root.join("gates/structural.sh");
    let mut cmd = Command::new("bash");
    cmd.arg(&script);
    if scope == scope::Scope::Staged {
        cmd.arg("--staged");
    }
    match cmd.status() {
        Ok(status) => status.code().unwrap_or(1),
        Err(e) => {
            eprintln!("✗ goh: failed to exec {}: {e}", script.display());
            1
        }
    }
}

/// Fail on merge-conflict markers. Returns 0 clean, 1 violations, 2 on error.
fn run_markers(staged: bool) -> i32 {
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        eprintln!("⚠ [no_conflict_markers] not a git repo — skipping");
        return 0;
    };
    match markers::scan_root(&root, staged) {
        Err(message) => {
            eprintln!("✗ [no_conflict_markers] {message}");
            2
        }
        Ok(bad) if !bad.is_empty() => {
            eprintln!("✗ [no_conflict_markers] merge conflict markers found:");
            for hit in &bad {
                eprintln!("    {}:{}: {}", hit.path, hit.line_no, hit.text);
            }
            1
        }
        Ok(_) => {
            let scope = if staged { "staged" } else { "tracked" };
            println!("→ [no_conflict_markers] OK — no markers in {scope} files");
            0
        }
    }
}

/// Fail on source files over `max` lines. Returns 0 clean, 1 violations, 2 on error.
fn run_length(max: usize, exclude: &str, staged: bool) -> i32 {
    let filter = match compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [file_length] {message}");
            return 2;
        }
    };
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        eprintln!("⚠ [file_length] not a git repo — skipping");
        return 0;
    };
    match length::scan_root(&root, max, filter.as_ref(), staged) {
        Err(message) => {
            eprintln!("✗ [file_length] {message}");
            2
        }
        Ok((over, _checked)) if !over.is_empty() => {
            eprintln!(
                "✗ [file_length] {} file(s) over the {max}-line cap:",
                over.len()
            );
            for hit in &over {
                eprintln!(
                    "    {:>6} lines  {}  (+{})",
                    hit.lines,
                    hit.path,
                    hit.lines - max
                );
            }
            eprintln!(
                "\n  Split them. If a file genuinely cannot be split (vendored or\n  generated), add it to GOH_LINE_EXCLUDE in .gatesrc — with a reason."
            );
            1
        }
        Ok((_, checked)) => {
            println!("→ [file_length] OK — {checked} file(s) within {max} lines");
            0
        }
    }
}

/// Fail on disallowed emoji. Returns 0 clean, 1 violations, 2 on error.
fn run_emoji(exclude: &str, allow: &str, staged: bool) -> i32 {
    let filter = match compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [no_emoji] {message}");
            return 2;
        }
    };
    let extra = emoji::parse_allow(allow);
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_emoji] not a git repo — skipping");
        return 0;
    };
    match emoji::scan_root(&root, filter.as_ref(), &extra, staged) {
        Err(message) => {
            eprintln!("✗ [no_emoji] {message}");
            2
        }
        Ok((hits, _checked)) if !hits.is_empty() => {
            // NOTE: violations go to stdout, matching the reference checker
            // (the markers/length checkers use stderr — each port mirrors
            // its own reference, not the family average).
            let scope = if staged { "staged" } else { "tracked" };
            let mut permit = emoji::ALLOWED_ORDERED
                .iter()
                .map(char::to_string)
                .collect::<Vec<_>>()
                .join(" ");
            if !allow.is_empty() {
                permit.push_str(" + ");
                permit.push_str(allow);
            }
            println!(
                "✗ DISALLOWED EMOJI in {} location(s) ({scope}) — only the Kare icon set and functional typographic glyphs are permitted ({permit}); © ® ™ are typographic signs with legal meaning, but their emoji-presentation VS16 forms fail:",
                hits.len()
            );
            for hit in hits.iter().take(200) {
                println!(
                    "  {}:{}:{}: U+{:04X} '{}'",
                    hit.path, hit.lineno, hit.col, hit.ch as u32, hit.ch
                );
            }
            if hits.len() > 200 {
                println!("  … and {} more", hits.len() - 200);
            }
            1
        }
        Ok((_, checked)) => {
            let scope = if staged { "staged" } else { "tracked" };
            println!("✓ [no_emoji] OK — {checked} {scope} files clean");
            0
        }
    }
}
