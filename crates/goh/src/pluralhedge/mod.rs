//! `goh plural-hedge`: a hedged plural in a string literal is a finding (GOH.PLURALHEDGE).
//!
//! `0 open entr(ies)`, `4 step(s)`, `2 finding(s)`: a message that never decided which number it
//! was talking about. The house helper decides it -- `counted`/`noun` in `tui.lib`, `counted`/
//! `plural_noun` in `tui/lib.sh`, `goh_tui::counted`/`noun` in Rust -- so `1 file`, `0 files` and
//! `3 files` come out right. A private Rust app carried the first copy of this check
//! (`plural::tests::no_user_facing_string_hedges_with_an_s_in_parentheses`, which caught
//! RT.WEBKITPROOF); this is the house one, over `.py`, `.sh` (and every shell source by shebang),
//! `.rs` and `.swift`. The finder is `scan.rs`.
//!
//! NOT SEEN: a hedge on a middle line of a multi-line string (a heredoc, a docstring body), or a
//! plural assembled from pieces (`"file" + "(s)"`).
//!
//! WIRING: a structural step in EVERY repo. The estate's sites on the day it landed are seeded in
//! `plural_hedge_seed.json` (`crate::estate_seed`): seeded files warn, anything else is red.
//
// `goh plural-hedge`: A hedged plural (`file(s)`, `process(es)`, `entr(ies)`, `{unit}(s)`) inside a
// string literal of a `.py`, `.sh` (or shell by shebang), `.rs` or `.swift` file is a finding
// (GOH.PLURALHEDGE): a count meets its noun through `counted`/`noun` (`tui.lib`),
// `counted`/`plural_noun` (`tui/plural.sh`, which `tui/lib.sh` and every gate source) or
// `goh_tui::counted`/`noun`. Line-based; whole-line and trailing comments are not read; `http(s)`,
// a call in a code fragment (`x.log(s)`, `def close(s):`) and a builtin shown called (`print(s)`,
// `len(s)`) are not hedges; a `"$(...)"` substitution's own strings are read; a middle line of a
// multi-line string is not seen. A structural step in every repo, against the root-commit-keyed
// estate seed `plural_hedge_seed.json`. Lifted from a private Rust app's `plural::tests`. A seeded
// file's warning lists five sites and counts the rest; `--all` lists every one (GOH.HEDGELIST).
// Source: `crates/goh/src/pluralhedge/`, `crates/goh/src/estate_seed.rs`. Pinned by
// `pluralhedge/tests.rs`, `test_plural_hedge.py`.

pub mod scan;

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

const TAG: &str = "[plural_hedge]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no hedged plural in a string";

/// The seed of the estate's sites on the day the check landed.
pub const KIND: Kind = Kind {
    file: "plural_hedge_seed.json",
    unit: "hedged plural",
    name: "plural-hedge seed",
};

const FIX: &str = "    A count meets its noun in the tui lib, so `1 file`, `0 files` and `3 files` come out right:\n      \
     Python  from tui.lib import counted, noun     counted(n, \"file\"), counted(n, \"entry\", \"entries\")\n      \
     shell   . \"$GOH_DIR/tui/lib.sh\"               \"$(counted \"$n\" file)\", \"$(plural_noun \"$n\" entry entries)\"\n      \
     Rust    goh_tui::counted(n, \"file\")           goh_tui::counted_as(n, \"entry\", \"entries\")\n";

/// The lexing a tracked file gets, `None` when it is not in the subject.
fn lang(root: &Path, rel: &str, staged: bool) -> Option<scan::Lang> {
    let ext = Path::new(rel)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("");
    match ext.to_ascii_lowercase().as_str() {
        "rs" | "swift" => Some(scan::Lang::Slash),
        "py" | "sh" | "bash" | "zsh" => Some(scan::Lang::Hash),
        "" => {
            let head = crate::gitutil::content_bytes(root, rel, staged).unwrap_or_default();
            crate::shellsrc::is_shell(rel, &head[..head.len().min(256)]).then_some(scan::Lang::Hash)
        }
        _ => None,
    }
}

/// The sites in the subject among `files`.
fn sites(
    root: &Path,
    files: &[String],
    (exclude, staged): (&str, bool),
    continuation: bool,
) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let mut read = 0;
    let mut out = Sites::new();
    for rel in files {
        if exclude.as_ref().is_some_and(|x| x.is_match(rel)) {
            continue;
        }
        let Some(lang) = lang(root, rel, staged) else {
            continue;
        };
        let Some(text) = crate::gitutil::content_bytes(root, rel, staged)
            .and_then(|b| String::from_utf8(b).ok())
        else {
            continue;
        };
        read += 1;
        let lines: Vec<&str> = text.lines().collect();
        let found: Vec<String> = scan::findings_in(&text, lang, continuation)
            .into_iter()
            .map(|f| {
                format!(
                    "    {rel}:{}: {}  -- `{}` hedges: count it with counted/noun",
                    f.line,
                    estate_seed::clip(lines[f.line - 1]),
                    f.hedge
                )
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    crate::subject::record(&crate::subject::PLURAL_HEDGE, read);
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`. `all` lists every seeded site.
fn judge(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
    all: bool,
) -> (i32, String, String) {
    use goh_tui::ERR;
    match estate_seed::load(root, KIND, staged) {
        Ok(seed) => judge_seeded(root, files, (exclude, staged), &seed, all),
        Err(m) => (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    }
}

/// `judge` over a loaded `seed`.
fn judge_seeded(
    root: &Path,
    files: &[String],
    (exclude, staged): (&str, bool),
    seed: &estate_seed::Seed,
    all: bool,
) -> (i32, String, String) {
    use goh_tui::ERR;
    let over = crate::grace::over();
    let mut found = match sites(root, files, (exclude, staged), over) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    // The continuation shape is judged from the grace's end: shown, and a seeded site held, now.
    let mut warn = String::new();
    if !over {
        if let Ok(day) = sites(root, files, (exclude, staged), true) {
            let seeded = crate::grace::ahead::seeded(root, seed);
            let shown = crate::grace::ahead::shown(TAG, found, &day, &seeded);
            (found, warn) = (shown.held, shown.warn);
        }
    }
    let report = Report {
        tag: TAG,
        item: "GOH.PLURALHEDGE",
        what: ("hedged plural", "hedged plurals"),
        fix: FIX,
        ok: "every count in a string meets its noun".to_string(),
    };
    let v = estate_seed::verdict_listed(root, staged, KIND, seed, &found, &report, all);
    crate::grace::ahead::with(v, &warn)
}

/// `goh plural-hedge [--staged] [--exclude RE] [--all]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str, all: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, exclude, staged, all)),
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
        "crates/goh/src/pluralhedge/mod.rs",
        judge(repo, files, &cfg.exclude, staged, false),
    )
}

#[cfg(test)]
mod tests;
