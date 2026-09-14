//! Single-checker subcommands (`markers`, `length`, `emoji`, `secrets`).
//! Extracted from `main.rs`: past the cap, the split is the fix.

use std::path::PathBuf;

use crate::{emoji, gitutil, length, markers, secrets, steps};

pub fn run_markers(staged: bool) -> i32 {
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
            eprint!("{}", markers::format_report(&bad));
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
pub fn run_length(max: usize, exclude: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
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
            eprint!("{}", length::format_report(&over, max));
            1
        }
        Ok((_, checked)) => {
            println!("→ [file_length] OK — {checked} file(s) within {max} lines");
            0
        }
    }
}

/// Fail on disallowed emoji. Returns 0 clean, 1 violations, 2 on error.
pub fn run_emoji(exclude: &str, allow: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
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
            print!("{}", emoji::format_report(&hits, staged, allow));
            1
        }
        Ok((_, checked)) => {
            let scope = if staged { "staged" } else { "tracked" };
            println!("✓ [no_emoji] OK — {checked} {scope} files clean");
            0
        }
    }
}

/// Fail on committed secrets. Returns 0 clean, 1 violations, 2 on error.
pub fn run_secrets(exclude: &str, staged: bool) -> i32 {
    let filter = match steps::compile_exclude(exclude) {
        Ok(filter) => filter,
        Err(message) => {
            eprintln!("✗ [no_secrets] {message}");
            return 2;
        }
    };
    let Some(root) = gitutil::repo_root().map(PathBuf::from) else {
        println!("[no_secrets] not a git repo — skipping");
        return 0;
    };
    match secrets::scan_root(&root, filter.as_ref(), staged) {
        Err(message) => {
            eprintln!("✗ [no_secrets] {message}");
            2
        }
        // NOTE: violations go to stdout, matching the reference checker.
        Ok((bad, _checked)) if !bad.is_empty() => {
            print!("{}", secrets::format_report(&bad, staged));
            1
        }
        Ok((_, checked)) => {
            let scope = if staged { "staged" } else { "tracked" };
            println!("✓ [no_secrets] OK — {checked} {scope} files clean");
            0
        }
    }
}
