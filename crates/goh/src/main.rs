//! `goh` — static structural-gate binary.
//!
//! `structural` is the whole layer-1 pipeline, every step native but the two
//! `--full` sweeps it delegates to `$GOH_DIR/checks` (the empty-scope and
//! self-proof sweeps, which judge a repo's OWN Python gates). Every other
//! subcommand is one house check, run as `gates/goh.sh <check>`. The Python
//! checkers these port are retired (Phase N3); `gates/structural.sh` execs
//! this binary and refuses without it. `tests/test_goh_structural.py` pins
//! the pipeline's verdicts and its step inventory.

#![deny(unsafe_code)]

pub mod blobs;
pub mod bounded;
pub mod canarycmd;
pub mod ceiling;
pub mod claims;
pub mod cli;
pub mod commands;
pub mod commit_class;
pub mod credurls;
pub mod deps;
pub mod emoji;
pub mod emptyassert;
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
pub mod pathfilter;
pub mod platform;
pub mod prefetch;
pub mod proven;
pub mod provenance;
pub mod pyformat;
pub mod pyjson;
pub mod pylex;
pub mod ratchet;
pub mod requires_call;
pub mod requires_call_py;
pub mod rust_depinfo;
pub mod rust_scope;
pub mod scope;
pub mod screen;
pub mod screen_mask;
pub mod screen_shapes;
pub mod secrets;
pub mod shell_lint;
pub mod skills;
pub mod skills_audit;
pub mod step_report;
pub mod stepcmd;
pub mod steps;
pub mod steps_delegated;
pub mod steps_rust;
pub mod structural;
pub mod tagver;
pub mod unreaped;
pub mod vendored;
pub mod verdict_cache;
pub mod versrc;

use std::path::PathBuf;

use clap::Parser;
use cli::{Cli, Commands};

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
    let code = match run_ported(cli.command) {
        Ok(code) => code,
        Err(command) => run_core(*command),
    };
    std::process::exit(code);
}

/// The commands that are not Phase N1 ports.
fn run_core(command: Commands) -> i32 {
    match command {
        Commands::RustScope {
            cargo_dir,
            check_depinfo,
        } => rust_scope::run_command(&cargo_dir, check_depinfo.as_deref()),
        Commands::SourceTree => {
            println!("{}", env!("GOH_SOURCE_STAMP"));
            0
        }
        Commands::Structural { staged, full } => structural::run(staged, full),
        Commands::Canary {
            repo,
            log,
            snapshot,
            timeout,
            grace,
            label,
            verbose: _,
            command,
        } => canarycmd::run(&canarycmd::Args {
            repo,
            log,
            snapshot,
            timeout,
            grace,
            label,
            command,
        }),
        Commands::CommitClass {
            file,
            range,
            report,
        } => commit_class::run(file.as_deref(), &range, report),
        Commands::RequiresCall { rules, root } => {
            requires_call::run(rules.as_deref(), root.as_deref())
        }
        Commands::Proven { action } => match action {
            cli::ProvenAction::Key { step, entries } => proven::key(&step, &entries),
            cli::ProvenAction::Lookup { key, step, ttl } => proven::lookup(&key, &step, ttl),
            cli::ProvenAction::Record {
                key,
                tree,
                step,
                label,
                ttl,
            } => proven::record(&key, &tree, &step, &label, ttl),
        },
        Commands::Step {
            timeout,
            grace,
            label,
            command,
        } => stepcmd::run(&timeout, grace, &label, &command),
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
        Commands::Lints { self_test } => commands::run_lints(self_test),
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
        other => {
            eprintln!("✗ goh: {other:?} has no runner");
            2
        }
    }
}

/// The Phase N1 ports, or the command back for `run_core`.
fn run_ported(command: Commands) -> Result<i32, Box<Commands>> {
    Ok(match command {
        Commands::VersionProvenance {
            root,
            baseline,
            staged,
            json,
        } => provenance::run_command(&root, baseline.as_deref(), staged, json),
        Commands::ShellLint { exclude, staged } => shell_lint::run_command(staged, &exclude),
        Commands::PythonFormatted {
            trees,
            staged,
            selftest,
        } => pyformat::run_command(&trees, staged, selftest),
        Commands::CredentialUrls {
            root,
            json,
            verdict,
        } => credurls::run_command(root.as_deref(), json, verdict.as_deref()),
        Commands::Deps {
            root,
            json,
            offline,
            strict,
            ratchet,
        } => deps::run_command(root.as_deref(), json, offline, strict, ratchet.as_deref()),
        Commands::EmptyAssert { exclude, staged } => emptyassert::run_command(staged, &exclude),
        Commands::TagVersion {
            root,
            refs_file,
            json,
            extract,
        } => extract.map_or_else(
            || tagver::run_command(root.as_deref(), refs_file.as_deref(), json),
            |kind| tagver::extract_command(&kind),
        ),
        Commands::LockVersion { root, json } => lockver::run_command(root.as_deref(), json),
        Commands::MdLinks {
            root,
            staged,
            exclude,
            json,
            anchors,
        } => mdlinks::run_command(root.as_deref(), staged, exclude.as_deref(), json, anchors),
        Commands::ClaimDerivation {
            parse_claims: Some(rel),
            ..
        } => claims::parse_claims_command(&rel),
        Commands::ClaimDerivation {
            root,
            staged,
            exclude,
            json,
            parse_claims: None,
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
        other => return Err(Box::new(other)),
    })
}
