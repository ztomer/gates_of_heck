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
//! WIRING: a structural step in EVERY repo, judged from `crate::grace::OVER_FROM` (2026-11-23):
//! before it the step judges nothing, as the last release did not, so a consumer is not red before
//! its seed exists. The estate's sites on the day it landed are seeded in `tui_marks_seed.json`
//! (`crate::estate_seed`): seeded files warn, anything else is red.

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
    let mut out = Sites::new();
    for ((rel, text), lang) in langs {
        let found = scan::findings(text, lang)?;
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

/// The verdict over `files`: `(exit code, stdout, stderr)`. Nothing is judged within the grace.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    judge_at(root, files, exclude, staged, crate::grace::over())
}

/// `judge` with the grace decided (`crate::grace`).
fn judge_at(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    over: bool,
) -> (i32, String, String) {
    use goh_tui::ERR;
    if !over {
        return (0, String::new(), String::new());
    }
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
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
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
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
