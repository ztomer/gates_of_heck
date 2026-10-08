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
pub mod checkoutcreds;
pub mod claims;
pub mod cli;
pub mod commands;
pub mod commit_class;
pub mod credurls;
pub mod deadexec;
pub mod deps;
pub mod earlypipe;
pub mod emoji;
pub mod emptyassert;
pub mod gatesrc;
pub mod gitutil;
pub mod homepaths;
pub mod hookindex;
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
pub mod revblobs;
pub mod rust_depinfo;
pub mod rust_scope;
pub mod scope;
pub mod screen;
pub mod screen_mask;
pub mod screen_shapes;
pub mod secrets;
pub mod shell_lint;
pub mod shellsrc;
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
    let code = match run_ported(cli.command).or_else(|command| run_scanners(*command)) {
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
            clusters,
        } => commit_class::run(file.as_deref(), &range, report, clusters),
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
        Commands::Markers { staged, commits } => commands::run_markers(staged, &commits),
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

/// A direct check's exclusion: `--exclude`, else the repo's declared `GOH_EXCLUDE`
/// (`gatesrc::declared_exclude`). An unreadable `.gatesrc` is a usage error (exit 2), never a
/// run over an exemption nobody could read.
fn declared(
    flag: Option<String>,
    root: Option<&str>,
    which: gatesrc::Exempt,
) -> Result<String, i32> {
    gatesrc::declared_exclude(flag, root.map(std::path::Path::new), which).map_err(|message| {
        eprintln!("✗ goh: {message}");
        2
    })
}

/// An empty pattern means "no exemption" to the checks that take an `Option`.
fn nonempty(pattern: &str) -> Option<&str> {
    (!pattern.is_empty()).then_some(pattern)
}

/// The path scanners (`--exclude` resolved against the repo's `.gatesrc`), or the command back.
fn run_scanners(command: Commands) -> Result<i32, Box<Commands>> {
    Ok(match command {
        Commands::Length {
            max,
            exclude,
            staged,
        } => declared(exclude, None, gatesrc::Exempt::Length)
            .map_or_else(|code| code, |x| commands::run_length(max, &x, staged)),
        Commands::Emoji {
            exclude,
            allow,
            staged,
        } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| commands::run_emoji(&x, &allow, staged)),
        Commands::Secrets { staged } => commands::run_secrets(staged),
        Commands::HomePaths { exclude, staged } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| commands::run_home_paths(&x, staged)),
        Commands::NoAllow { exclude, staged } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| commands::run_no_allow(&x, staged)),
        other => return Err(Box::new(other)),
    })
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
        Commands::ShellLint { exclude, staged } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| shell_lint::run_command(staged, &x)),
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
        Commands::EmptyAssert { exclude, staged } => {
            declared(exclude, None, gatesrc::Exempt::Paths)
                .map_or_else(|code| code, |x| emptyassert::run_command(staged, &x))
        }
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
        } => declared(exclude, root.as_deref(), gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| mdlinks::run_command(root.as_deref(), staged, nonempty(&x), json, anchors),
        ),
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
        } => declared(exclude, root.as_deref(), gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| claims::run_command(root.as_deref(), staged, nonempty(&x), json),
        ),
        Commands::KillByName {
            exclude,
            staged,
            code_lines,
        } => declared(exclude, None, gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| killname::run_command(staged, &x, code_lines.as_deref()),
        ),
        Commands::EarlyExitPipe { exclude, staged } => {
            declared(exclude, None, gatesrc::Exempt::Paths)
                .map_or_else(|code| code, |x| earlypipe::run_command(staged, &x))
        }
        Commands::DeadAfterExec { exclude, staged } => {
            declared(exclude, None, gatesrc::Exempt::Paths)
                .map_or_else(|code| code, |x| deadexec::run_command(staged, &x))
        }
        Commands::BareHookIndex { exclude, staged } => {
            declared(exclude, None, gatesrc::Exempt::Paths)
                .map_or_else(|code| code, |x| hookindex::run_command(staged, &x))
        }
        Commands::CheckoutCredentials { staged } => checkoutcreds::run_command(staged),
        Commands::UnreapedSpawn {
            exclude,
            staged,
            verdicts,
        } => declared(exclude, None, gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| unreaped::run_command(staged, &x, verdicts.as_deref()),
        ),
        other => return Err(Box::new(other)),
    })
}
