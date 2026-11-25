//! `goh tui-marks`: a script that prints its own Kare mark is a finding (GOH.TUIMARKS, house rule 2).
//!
//! The house prints a status line through `tui/lib.sh` / `tui.lib` (`info ok err warn die`), which
//! owns the icons, the colours, `NO_COLOR`, the tty test and the stderr split. `echo "✗ failed"`
//! or `printf '  ✓ %s\n'` does none of that. `check_no_emoji.py` allows the marks by policy, and
//! `goh status-markers` (bracketed markers, an opt-in) does not look at the shape, so `koffee_big`'s
//! `setup_android.sh` and `verify_apk.sh` hand-rolled them unseen and grew a repo-local check
//! (KB.TUILIB, 2026-11-12) -- a vendored house gate, which this replaces. The finder is `scan.rs`.
//!
//! THE SUBJECT is bare-open's, read through the same two selectors (`crate::shellsrc::sources`,
//! `crate::bareopen_py::sources`), so `GOH_BARE_OPEN_MIN_FILES` is the floor that sees a
//! selection gone blind; this scan records no key of its own.
//!
//! EXEMPT: goh's own `tui/lib.sh` and `tui/lib.py`, which ARE the house lib (keyed by goh's root
//! commit, so a consumer's vendored copy is not); and a git hook (`.githooks/<hook>`, goh's stock
//! `hooks/<hook>`), the bootstrap path: it runs before goh is found and its marks say goh was NOT
//! found, so it cannot print through a lib it is looking for. Every consumer carries goh's five
//! stock hooks, so without this each repo would seed 13 sites only goh can change. Anything else
//! in that position says why on the line (`scan::MARKER`).
//!
//! THE FALLBACK OWNER (GOH.ESCAPEOWNER, 2026-11-15). An escape (`\033[`) or a bracketed marker
//! (`[ OK ]`) opening a print is `goh status-markers`' token, and status-markers is an opt-in (four
//! repos run it), so before this a repo that had not opted in judged those prints nowhere. This
//! step judges them as status-markers' FALLBACK: a fallback finding is dropped exactly where
//! status-markers counts the same line -- the repo's push runs it ([`status_markers_runs`]: the
//! command in `tools/gate.sh`, `GOH_CI_STEPS` or a script of the repo's they name, read as
//! `crate::layerparity::push_text` reads them) and it reads the file (`crate::statusmark::is_source`)
//! and finds that line. One owner per site: never counted twice, and never left to nobody (a
//! shebang-only script status-markers does not read stays this step's).
//!
//! WIRING: a structural step in EVERY repo, judged from `crate::grace::OVER_FROM` (2026-11-23):
//! before it the step judges nothing, as the last release did not, so a consumer is not red before
//! its seed exists. The estate's sites on the day it landed are seeded in `tui_marks_seed.json`
//! (`crate::estate_seed`): seeded files warn, anything else is red.
//
// `goh tui-marks`: A shell `echo`/`printf` or Python `print` whose text opens with a Kare mark (`→
// · ✓ ✗ ⚠`) is a finding (GOH.TUIMARKS, house rule 2): `tui/lib.sh` / `tui.lib` print it. One
// opening with an ANSI escape or a bracketed marker, `goh status-markers`' tokens, is its FALLBACK
// (GOH.ESCAPEOWNER): judged unless the repo's push runs status-markers and status-markers counts
// that line, so each site has one owner and none has none. Line-based; whole-line comments are not
// read; the reasoned escape is `# tui-mark-ok: <reason>` on the line. Exempt: goh's own
// `tui/lib.sh`/`tui/lib.py` (by goh's root commit) and git hooks (`.githooks/<hook>`,
// `hooks/<hook>`), the bootstrap path that reports goh missing. Reads bare-open's subject through
// its selectors, so `GOH_BARE_OPEN_MIN_FILES` is its floor. A structural step in every repo judged
// from 2026-11-23 (`crate::grace`), against the root-commit-keyed estate seed
// `tui_marks_seed.json`; replaces koffee_big's local `tools/check_tui_marks.py`. Pinned by
// `tuimarks/tests.rs`.

pub mod scan;

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

const TAG: &str = "[tui_marks]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no hand-rolled TUI mark";

/// The seed of the estate's sites on the day the check landed.
pub const KIND: Kind = Kind {
    file: "tui_marks_seed.json",
    unit: "hand-rolled mark",
    name: "tui-marks seed",
};

/// goh's root commit: its `tui/` lib is the one place the marks are printed by hand.
const GOH_ROOT: &str = "73391f04f5f2b92ab9813c62e6cecdce919d2d86";
/// The house lib's own files, exempt in goh alone.
const HOUSE_LIB: [&str; 2] = ["tui/lib.sh", "tui/lib.py"];

const FIX: &str = "    Print through the house lib, which owns the marks, the colours, NO_COLOR and the tty test:\n      \
     shell   . \"$GOH_DIR/tui/lib.sh\"     info \"...\"  ok \"...\"  warn \"...\"  err \"...\"  die \"...\"\n      \
     Python  from tui.lib import info, ok, warn, err, die    (PYTHONPATH=$GOH_DIR)\n";

/// The client-side hooks git runs, by name.
const GIT_HOOKS: [&str; 14] = [
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "post-rewrite",
    "reference-transaction",
];

/// Whether `rel` is a git hook: a hook's name directly under `.githooks/` or `hooks/`.
#[must_use]
pub fn is_git_hook(rel: &str) -> bool {
    rel.rsplit_once('/').is_some_and(|(dir, name)| {
        (dir == ".githooks" || dir == "hooks") && GIT_HOOKS.contains(&name)
    })
}

/// Whether `rel` in the repository whose root commit is `root` is exempt: the house lib itself,
/// or a git hook.
#[must_use]
pub fn exempt(root: &str, rel: &str) -> bool {
    root == GOH_ROOT && HOUSE_LIB.contains(&rel) || is_git_hook(rel)
}

/// The language `rel` (holding `text`) is read as, by the same two selectors [`sites`] reads
/// through (`crate::shellsrc::is_shell`, and `bareopen_py`'s `.py`), or `None`: not read.
#[must_use]
pub fn lang_of(rel: &str, text: &str) -> Option<scan::Lang> {
    if crate::shellsrc::is_shell(rel, text.as_bytes()) {
        return Some(scan::Lang::Shell);
    }
    Path::new(rel)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("py"))
        .then_some(scan::Lang::Python)
}

/// Whether a push of the repository at `root` runs `goh status-markers` (its own gate, its
/// `GOH_CI_STEPS`, or a script of its own they name; comment lines are not run).
#[must_use]
pub fn status_markers_runs(root: &Path, files: &[String], staged: bool) -> bool {
    static RUNS: std::sync::LazyLock<regex::Regex> = std::sync::LazyLock::new(|| {
        regex::Regex::new(r#"\bgoh(?:\.sh)?["']?\s+status-markers\b"#)
            .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
    });
    let tree: std::collections::BTreeSet<String> = files.iter().cloned().collect();
    RUNS.is_match(&crate::layerparity::push_text(root, &tree, staged))
}

/// The lines of `rel` that `goh status-markers` counts, where `runs` (it is run here).
fn status_lines(runs: bool, rel: &str, text: &str) -> Result<Vec<usize>, String> {
    if !runs || !crate::statusmark::is_source(rel) {
        return Ok(Vec::new());
    }
    Ok(crate::statusmark::scan::findings(text)?
        .into_iter()
        .map(|f| f.line)
        .collect())
}

/// The sites in the shell and Python sources among `files`.
fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let shell = crate::shellsrc::sources(root, files, exclude.as_ref(), staged);
    let python = crate::bareopen_py::sources(root, files, exclude.as_ref(), staged);
    let langs = shell
        .iter()
        .map(|s| (s, scan::Lang::Shell))
        .chain(python.iter().map(|s| (s, scan::Lang::Python)));
    let mut identity: Option<String> = None;
    let mut runs: Option<bool> = None;
    let mut out = Sites::new();
    for ((rel, text), lang) in langs {
        let mut found = scan::findings(text, lang)?;
        if found.iter().any(|f| f.fallback) {
            let runs = *runs.get_or_insert_with(|| status_markers_runs(root, files, staged));
            let theirs = status_lines(runs, rel, text)?;
            found.retain(|f| !(f.fallback && theirs.contains(&f.line)));
        }
        if found.is_empty() {
            continue;
        }
        let me =
            identity.get_or_insert_with(|| crate::gitutil::root_commit(root).unwrap_or_default());
        if exempt(me, rel) {
            continue;
        }
        let rows: Vec<&str> = text.lines().collect();
        let listed = found
            .into_iter()
            .map(|f| {
                format!(
                    "    {rel}:{}: {}  -- `{}` by hand: print it with info/ok/warn/err",
                    f.line,
                    estate_seed::clip(rows.get(f.line - 1).copied().unwrap_or("")),
                    f.mark
                )
            })
            .collect();
        out.insert(rel.clone(), listed);
    }
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`. Nothing is judged within the grace, but
/// the run is still made: see `judge_at`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    judge_at(root, files, exclude, staged, crate::grace::over())
}

/// `judge` with the grace decided (`crate::grace`).
///
/// The grace changes the VERDICT, never the RECORD (GOH.TUIMARKSSETTLE): within it the run is made
/// all the same, so a commit that fixes a seeded mark journals its settle and `goh seed-settle`
/// retires the entry, and the stale entries do not pile up unseen to meet each consumer as a wall
/// of red at its first push after `crate::grace::OVER_FROM`. Its verdict is then discarded, a
/// failed read of the seed included: exit 0, and the sites the day will find red as the warning
/// (GOH.GRACESHAPES).
fn judge_at(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    over: bool,
) -> (i32, String, String) {
    let (judged, found) = judge_all(root, files, exclude, staged);
    if over {
        judged
    } else {
        crate::grace::ahead::warn_only((root, staged), (KIND, TAG), Ok(found))
    }
}

/// The verdict of the full run, the grace or not, and the sites it judged (none when it refused).
fn judge_all(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
) -> ((i32, String, String), Sites) {
    use goh_tui::ERR;
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => {
            return (
                (2, String::new(), format!("{ERR} {TAG} {m}\n")),
                Sites::new(),
            )
        }
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => {
            return (
                (2, String::new(), format!("{ERR} {TAG} {m}\n")),
                Sites::new(),
            )
        }
    };
    let report = Report {
        tag: TAG,
        item: "GOH.TUIMARKS",
        what: (
            "hand-rolled TUI mark the house lib prints",
            "hand-rolled TUI marks the house lib prints",
        ),
        fix: FIX,
        ok: "every status line goes through the house TUI lib".to_string(),
    };
    (
        estate_seed::verdict(root, staged, KIND, &seed, &found, &report),
        found,
    )
}

/// `goh tui-marks [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, exclude, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/tuimarks/mod.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests;
