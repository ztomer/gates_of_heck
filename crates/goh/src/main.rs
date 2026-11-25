//! `goh` — static structural-gate binary.
//!
//! `structural` is the whole layer-1 pipeline, every step native but the two
//! `--full` sweeps it delegates to `$GOH_DIR/checks` (the empty-scope and
//! self-proof sweeps, which judge a repo's OWN Python gates). Every other
//! subcommand is one house check, run as `gates/goh.sh <check>`. The Python
//! checkers these port are retired (Phase N3); `gates/structural.sh` execs
//! this binary and refuses without it. `tests/test_goh_structural.py` pins
//! the pipeline's verdicts and its step inventory.
//
// Every structural checker is native since Phase N3; the Python checkers they port are retired, and
// their measured behaviour is the frozen spec the parity suites run (`tests/reference_kit.py`,
// pinned to commit `96018bd`). `goh <check> --help` is each one's usage.
//
// `goh` (dispatch, flags): The entry point (`main.rs`: platform check, house scripts, the core
// commands), the Phase N1 ports and line scanners with their shared `--exclude` resolution
// (`ported.rs`), the subcommand list (`cli.rs`) and grouped flag structs (`cli_args.rs`; the ported
// path checks' in `cli_scan_args.rs`), split at the cap by concern (GOH.CAPHEADROOM). Source:
// `crates/goh/src/main.rs`, `ported.rs`, `cli.rs`, `cli_args.rs`, `cli_scan_args.rs`. Pinned by
// `crates/goh/tests/cli.rs`.

#![deny(unsafe_code)]

pub mod bareopen;
pub mod bareopen_py;
pub mod barepy;
pub mod blobs;
pub mod bounded;
pub mod busyloop;
pub mod cache_root;
pub mod callkey;
pub mod canarycmd;
pub mod ceiling;
pub mod changelog_fragments;
pub mod changelog_tags;
pub mod checkoutcreds;
pub mod claims;
pub mod cli;
pub mod cli_args;
pub mod cli_scan_args;
pub mod commands;
pub mod commit_class;
pub mod crates_behind;
pub mod crates_change;
pub mod credurls;
pub mod deadexec;
pub mod deadport;
pub mod decisions;
pub mod deps;
pub mod earlypipe;
pub mod emoji;
pub mod emptyassert;
pub mod estate_seed;
pub mod exit_status;
pub mod fixedsleep;
pub mod gatesrc;
pub mod gatesrc_dupes;
pub mod gitargv;
pub mod gitdeadline;
pub mod gitroot;
pub mod gitutil;
pub mod goh_config;
pub mod goh_dir;
pub mod gohpin;
pub mod grace;
pub mod homepaths;
pub mod hookindex;
pub mod house_cmds;
pub mod house_python;
pub mod index_view;
pub mod installs;
pub mod instline;
pub mod jssyntax;
pub mod killname;
pub mod launchapp;
pub mod layerparity;
pub mod length;
pub mod lints;
pub mod localeprefix;
pub mod lockver;
pub mod markers;
pub mod mdlinks;
pub mod mdtext;
pub mod mktempport;
pub mod movingtag;
pub mod noallow;
pub mod pathfilter;
pub mod platform;
pub mod pluralhedge;
pub mod ported;
pub mod prefetch;
pub mod privnames;
pub mod privnames_add;
pub mod proven;
pub mod provenance;
pub mod pyformat;
pub mod pyjson;
pub mod pylex;
pub mod ratchet;
pub mod red_run;
pub mod relquiet;
pub mod requires_call;
pub mod requires_call_py;
pub mod revblobs;
pub mod roadmap;
pub mod rubyshell;
pub mod run_path;
pub mod runpaths;
pub mod rust_depinfo;
pub mod rust_scope;
pub mod rust_scope_reads;
pub mod rustfmt_staged;
pub mod rustlex;
pub mod scope;
pub mod screen;
pub mod screen_mask;
pub mod screen_shapes;
pub mod secrets;
pub mod seedcount;
pub mod shapes;
pub mod shell_lint;
pub mod shell_lint_scope;
pub mod shell_lint_seed;
pub mod shellcheck_slots;
pub mod shellcheck_version;
pub mod shellsrc;
pub mod skills;
pub mod skills_audit;
pub mod statusmark;
pub mod step_report;
pub mod stepcmd;
pub mod steps;
pub mod steps_delegated;
pub mod steps_rust;
pub mod structural;
pub mod subject;
pub mod subject_required;
pub mod substdin;
pub mod tagpush;
pub mod tagver;
pub mod tapwrite;
pub mod tool_versions;
pub mod toolshell;
pub mod tuimarks;
pub mod unreaped;
pub mod urlexpand;
pub mod vendored;
pub mod verdict_cache;
pub mod versrc;
pub mod workflowpin;

use goh_tui::ERR;
use std::path::PathBuf;

use clap::{CommandFactory, FromArgMatches};
use cli::{Cli, Commands};
use ported::{declared, paths};

static ROOT: std::sync::OnceLock<Result<PathBuf, String>> = std::sync::OnceLock::new();

/// goh's checkout (`goh_dir::root_from`), resolved once: `GOH_DIR`, else the checkout this binary
/// sits in, else why neither names one.
fn resolved_root() -> &'static Result<PathBuf, String> {
    ROOT.get_or_init(|| {
        let exe = std::env::current_exe()
            .ok()
            .map(|p| p.canonicalize().unwrap_or(p));
        goh_dir::root_from(
            &|k| std::env::var_os(k),
            exe.as_deref(),
            std::path::Path::is_dir,
        )
    })
}

/// goh's checkout. `main` refuses to run without one (exit 2, naming why), so in the binary this
/// is always the resolved checkout, never a guessed home directory.
pub(crate) fn goh_root() -> PathBuf {
    resolved_root().clone().unwrap_or_default()
}

/// A plain `goh` says it ran (GOH.PLAINBINMARK): when the directory `LLVM_PROFILE_FILE` names
/// holds `.goh-binmark`, an empty `goh-<pid>.binmark` there. The suite's plain recording run gives
/// each test such a directory (`tests/_prof_record.py`), so a run that measures no profile still
/// learns which tests ran the binary -- through the one variable every route to an instrumented
/// run's profile carries -- and coverage may carry the rest. Written first, before any refusal: a
/// run that refused still ran. Any other profile directory is left alone; a failed write is a
/// test re-run, never an error. (One expression, no early return: the instrumented run that
/// measures these lines always has the variable set, so a `return` arm could never be counted.)
fn leave_run_mark() {
    let dir = std::env::var_os("LLVM_PROFILE_FILE").and_then(|p| {
        std::path::Path::new(&p)
            .parent()
            .map(std::path::Path::to_path_buf)
    });
    if let Some(dir) = dir.filter(|d| d.join(".goh-binmark").is_file()) {
        std::fs::write(dir.join(format!("goh-{}.binmark", std::process::id())), b"").ok();
    }
}

/// An instrumented build's run never profiles into its cwd (GOH.STRAYPROFRAW): with no
/// `LLVM_PROFILE_FILE` the profile goes under the temp directory, never the runtime's relative
/// default. `crates/goh/tests/stray_profile.rs`.
#[cfg(coverage)]
fn name_stray_profile() {
    use std::os::unix::ffi::OsStrExt;
    if std::env::var_os("LLVM_PROFILE_FILE").is_none_or(|v| v.is_empty()) {
        let path = std::env::temp_dir()
            .join("goh-profraw")
            .join("default_%m_%p.profraw");
        // No failure arm (GOH.SHIPRED01403): the path comes from the environment, which holds no
        // NUL byte, so the conversion never fails, and an `if let` arm for it was a line no run can
        // count (the ship's diff coverage named its closing brace).
        let c = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap_or_default();
        goh_sys::name_profile(Box::leak(c.into_boxed_c_str()));
    }
}

fn main() {
    #[cfg(coverage)]
    name_stray_profile();
    leave_run_mark();
    goh_dir::repair();
    if let Err(why) = resolved_root() {
        goh_tui::eoutln!("{ERR} {why}");
        std::process::exit(2);
    }
    let (os, arch) = platform::current();
    if let Err(reason) = platform::check(&os, &arch) {
        goh_tui::eoutln!("{ERR} {reason}");
        std::process::exit(1);
    }
    let matches = house_cmds::command(Cli::command()).get_matches();
    if let Some((name, script, args)) = house_cmds::chosen(&matches) {
        std::process::exit(house_script(name, script, &args));
    }
    let cli = Cli::from_arg_matches(&matches).unwrap_or_else(|e| e.exit());
    let code = match ported::run_ported(cli.command).or_else(|command| run_scanners(*command)) {
        Ok(code) => code,
        Err(command) => run_core(*command),
    };
    std::process::exit(code);
}

/// `goh ship`, `goh budget`...: the house Python on one goh entry point, its exit code passed on.
fn house_script(name: &str, rel: &str, args: &[String]) -> i32 {
    let py = match house_python::path() {
        Ok(py) => py,
        Err(why) => {
            goh_tui::eoutln!("{ERR} goh {name}: {why}");
            return 2;
        }
    };
    let script = goh_root().join(rel);
    std::process::Command::new(py)
        .arg(&script)
        .args(args)
        .status()
        .map_or_else(
            |e| {
                goh_tui::eoutln!("{ERR} goh {name}: cannot run {}: {e}", script.display());
                2
            },
            exit_status::code_of,
        )
}

/// The commands that are not Phase N1 ports.
fn run_core(command: Commands) -> i32 {
    match command {
        Commands::RustScope {
            cargo_dir,
            check_depinfo,
            compile,
        } => rust_scope::run_command(
            &cargo_dir,
            check_depinfo.as_deref(),
            if compile {
                rust_scope::Reads::Compile
            } else {
                rust_scope::Reads::Run
            },
        ),
        Commands::BareOpen(a) => paths(a.exclude, |x| bareopen::run_command(a.staged, x)),
        Commands::RunPaths(a) => paths(a.exclude, |x| runpaths::run_command(a.staged, x)),
        Commands::LaunchApp(a) => launchapp::run_args(a),
        Commands::SourceTree => {
            println!("{}", env!("GOH_SOURCE_STAMP"));
            0
        }
        Commands::Structural {
            staged,
            full,
            keep_going,
            only,
            list_steps,
        } => {
            if list_steps {
                structural::list_steps()
            } else {
                structural::run(staged, full, keep_going, &only)
            }
        }
        Commands::Canary(args) => canarycmd::run_cli(&args),
        Commands::CommitClass {
            file,
            range,
            report,
            clusters,
        } => commit_class::run(file.as_deref(), &range, report, clusters),
        Commands::RedRun { file, range } => red_run::run(file.as_deref(), &range),
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
                artifacts,
            } => proven::record(&key, &tree, &step, &label, ttl, &artifacts),
        },
        Commands::Step(args) => stepcmd::run_cli(&args),
        Commands::Markers { staged, commits } => commands::run_markers(staged, &commits),
        Commands::BusyLoop(args) => busyloop::run_command(args.staged),
        Commands::Screen {
            paths,
            scope,
            staged,
        } => commands::run_screen(&paths, scope.as_deref(), staged),
        Commands::Lints { self_test } => commands::run_lints(self_test),
        Commands::JsSyntax => jssyntax::command::run_command(),
        Commands::Decisions(a) => decisions::run_command(a.staged),
        Commands::Skills(args) => run_skills(&args),
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
            goh_tui::eoutln!("{ERR} goh: {other:?} has no runner");
            2
        }
    }
}

/// `goh skills`, its word ceiling from `--max-words`, else the repo's `.gatesrc` as structural
/// reads it (`gatesrc::skills_max_words`, BACKLOG 6.17).
fn run_skills(args: &cli_args::SkillsArgs) -> i32 {
    let max_words = args.max_words.map_or_else(
        || gatesrc::load(&gatesrc::config_root()).and_then(|c| gatesrc::skills_max_words(&c)),
        Ok,
    );
    match max_words {
        Ok(max_words) => commands::run_skills(
            args.path.as_deref().unwrap_or(&args.root),
            max_words,
            args.min_skills,
            args.baseline.as_deref(),
            args.update_baseline,
        ),
        Err(message) => {
            goh_tui::eoutln!("{ERR} goh: {message}");
            2
        }
    }
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
            .and_then(|x| declared(allow, None, gatesrc::Exempt::Glyphs).map(|g| (x, g)))
            .map_or_else(|code| code, |(x, g)| commands::run_emoji(&x, &g, staged)),
        Commands::Secrets { staged } => commands::run_secrets(staged),
        Commands::HomePaths { exclude, staged } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| commands::run_home_paths(&x, staged)),
        Commands::NoAllow { exclude, staged } => declared(exclude, None, gatesrc::Exempt::Paths)
            .map_or_else(|code| code, |x| commands::run_no_allow(&x, staged)),
        Commands::FixedSleep { exclude, seed } => {
            paths(exclude, |x| fixedsleep::run_command(seed, x))
        }
        Commands::ShellLint {
            exclude,
            staged,
            seed,
        } => paths(exclude, |x| shell_lint::run_command(staged, x, seed)),
        Commands::StatusMarkers { exclude, seed } => {
            paths(exclude, |x| statusmark::run_command(seed, x))
        }
        Commands::PrivateNames(a) => privnames::run_command(a.staged, &a.add),
        Commands::LayerParity(a) => layerparity::run_command(a.staged),
        Commands::Installs => installs::run_command(),
        Commands::ShellCheck => toolshell::run_shell_check(),
        Commands::ZshSplitGuard => toolshell::run_split_guard(),
        other => return Err(Box::new(other)),
    })
}
