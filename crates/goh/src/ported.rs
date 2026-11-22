//! The Phase N1 ports and the line scanners `goh` dispatches.
//!
//! Split from `main.rs` by concern (GOH.CAPHEADROOM), with the `--exclude` resolution they
//! share: a direct check's exclusion is `--exclude`, else the repo's declared `GOH_EXCLUDE`.

use crate::cli::{self, Commands};
use crate::{
    changelog_tags, checkoutcreds, claims, crates_behind, credurls, deadexec, deadport, deps,
    earlypipe, emptyassert, estate_seed, gatesrc, gitdeadline, gohpin, hookindex, instline,
    killname, lockver, mdlinks, mktempport, movingtag, pluralhedge, provenance, pyformat, relquiet,
    roadmap, shapes, substdin, tagver, tapwrite, tuimarks, unreaped, workflowpin,
};
use goh_tui::ERR;

/// A direct check's exclusion: `--exclude`, else the repo's declared `GOH_EXCLUDE`
/// (`gatesrc::declared_exclude`). An unreadable `.gatesrc` is a usage error (exit 2), never a
/// run over an exemption nobody could read.
pub(crate) fn declared(
    flag: Option<String>,
    root: Option<&str>,
    which: gatesrc::Exempt,
) -> Result<String, i32> {
    gatesrc::declared_exclude(flag, root.map(std::path::Path::new), which).map_err(|message| {
        goh_tui::eoutln!("{ERR} goh: {message}");
        2
    })
}

/// An empty pattern means "no exemption" to the checks that take an `Option`.
pub(crate) fn nonempty(pattern: &str) -> Option<&str> {
    (!pattern.is_empty()).then_some(pattern)
}

/// A direct check whose ONLY exclusion is the repo's declared `GOH_EXCLUDE`: resolve it,
/// then run. Eight arms spelled this three-line wrapper out, which is what pushed this
/// function past the line cap on the ninth check -- so the wrapper is the function, and the
/// ninth check is one line instead of a copy.
pub(crate) fn paths<F: FnOnce(&str) -> i32>(exclude: Option<String>, run: F) -> i32 {
    declared(exclude, None, gatesrc::Exempt::Paths).map_or_else(|code| code, |x| run(&x))
}

/// `goh early-exit-pipe`: the seed write, or the scan.
fn early_pipe(seed: bool, staged: bool, exclude: &str) -> i32 {
    if seed {
        earlypipe::write_seed(exclude)
    } else {
        earlypipe::run_command(staged, exclude)
    }
}

/// The Phase N1 ports, or the command back for `run_core`.
pub(crate) fn run_ported(command: Commands) -> Result<i32, Box<Commands>> {
    Ok(match command {
        Commands::VersionProvenance(cli::VersionProvenanceArgs {
            root,
            baseline,
            staged,
            json,
        }) => provenance::run_command(&root, baseline.as_deref(), staged, json),
        Commands::PythonFormatted(cli::PythonFormattedArgs {
            trees,
            staged,
            selftest,
        }) => pyformat::run_command(&trees, staged, selftest),
        Commands::CredentialUrls(cli::CredentialUrlsArgs {
            root,
            json,
            verdict,
        }) => credurls::run_command(root.as_deref(), json, verdict.as_deref()),
        Commands::Deps(a) => deps::run_command(
            a.root.as_deref(),
            a.json,
            a.offline,
            a.strict,
            a.ratchet.as_deref(),
        ),
        Commands::CratesBehind(a) => crates_behind::run_command(&a),
        Commands::EmptyAssert { exclude, staged } => {
            paths(exclude, |x| emptyassert::run_command(staged, x))
        }
        Commands::TagVersion(args) => tagver::command(&args),
        Commands::LockVersion { root, json } => lockver::run_command(root.as_deref(), json),
        Commands::MdLinks(cli::MdLinksArgs {
            root,
            staged,
            exclude,
            json,
            anchors,
        }) => declared(exclude, root.as_deref(), gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| mdlinks::run_command(root.as_deref(), staged, nonempty(&x), json, anchors),
        ),
        Commands::ClaimDerivation(cli::ClaimDerivationArgs {
            parse_claims: Some(rel),
            ..
        }) => claims::parse_claims_command(&rel),
        Commands::ClaimDerivation(cli::ClaimDerivationArgs {
            root,
            staged,
            exclude,
            json,
            parse_claims: None,
        }) => declared(exclude, root.as_deref(), gatesrc::Exempt::Paths).map_or_else(
            |code| code,
            |x| claims::run_command(root.as_deref(), staged, nonempty(&x), json),
        ),
        Commands::KillByName(cli::KillByNameArgs {
            exclude,
            staged,
            code_lines,
        }) => paths(exclude, |x| {
            killname::run_command(staged, x, code_lines.as_deref())
        }),
        Commands::EarlyExitPipe(cli::EarlyExitPipeArgs {
            exclude,
            staged,
            seed,
        }) => paths(exclude, |x| early_pipe(seed, staged, x)),
        Commands::SeedSettle(a) => {
            let (code, said) = estate_seed::apply_pending(&a.pending);
            estate_seed::print((code, String::new(), said))
        }
        Commands::SeedAdd(a) => {
            let (code, said) = estate_seed::add_to_store(&a.file, &a.input);
            estate_seed::print((code, String::new(), said))
        }
        Commands::ChangelogTags(a) => changelog_tags::run_command(a.staged),
        Commands::CheckoutCredentials(a) => checkoutcreds::run_command(a.staged),
        Commands::TapWrites(a) => tapwrite::run_command(a.staged),
        Commands::MovingTags(a) => movingtag::run_command(a.staged),
        Commands::WorkflowPins(a) => workflowpin::run_command(a.staged),
        Commands::GohPins(a) => gohpin::run_command(a.staged),
        Commands::InstalledVersion(a) => instline::run_command(a.staged),
        Commands::Roadmap(a) => roadmap::run_command(a.staged, a.seed, a.queue.as_deref()),
        Commands::UnreapedSpawn(cli::UnreapedSpawnArgs {
            exclude,
            staged,
            verdicts,
        }) => paths(exclude, |x| {
            unreaped::run_command(staged, x, verdicts.as_deref())
        }),
        Commands::SubprocessStdin(cli::SubprocessStdinArgs {
            exclude,
            staged,
            source,
            seed,
        }) => paths(exclude, |x| {
            substdin::run_command(staged, Some(x), source.as_deref(), seed)
        }),
        other => return run_line_scans(other),
    })
}

/// The line scanners that take `--exclude`/`--staged`, or the command back for `run_core`.
fn run_line_scans(command: Commands) -> Result<i32, Box<Commands>> {
    Ok(match command {
        Commands::DeadAfterExec(a) => paths(a.exclude, |x| deadexec::run_command(a.staged, x)),
        Commands::BareHookIndex(a) => paths(a.exclude, |x| hookindex::run_command(a.staged, x)),
        Commands::ReleaseQuiet(a) => paths(a.exclude, |x| relquiet::run_command(a.staged, x)),
        Commands::MktempPortable(a) => paths(a.exclude, |x| mktempport::run_command(a.staged, x)),
        Commands::LocalePrefix(a) => {
            paths(a.exclude, |x| crate::localeprefix::run_command(a.staged, x))
        }
        Commands::PluralHedge(a) => paths(a.scope.exclude, |x| {
            pluralhedge::run_command(a.scope.staged, x, a.all)
        }),
        Commands::GitDeadline(a) => paths(a.exclude, |x| gitdeadline::run_command(a.staged, x)),
        Commands::DeadPort(a) => paths(a.exclude, |x| deadport::run_command(a.staged, x)),
        Commands::TuiMarks(a) => paths(a.exclude, |x| tuimarks::run_command(a.staged, x)),
        Commands::ShapeDiff { registry: true, .. } => shapes::print_registry(),
        Commands::ShapeDiff {
            roots,
            estate,
            since,
            scanner,
            ..
        } => shapes::diff::run_command(&roots, estate, since.as_deref(), scanner.as_deref()),
        other => return Err(Box::new(other)),
    })
}
