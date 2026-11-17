//! `goh layer-parity`: a language goh lints at COMMIT is linted whole at PUSH (GOH.LAYERPARITY,
//! the layer half of GOH.CLASS.PARITY's check 3).
//!
//! `gates/commit_gate.sh` lints every staged `.py` (`py_staged.sh`) and `.swift`
//! (`swift_staged.sh`), and the native structural step lints every staged `.rs` (`rustfmt`, no
//! `#[allow]`, emptiness asserts), whatever the repo's own `tools/gate.sh` says. The push half of
//! each -- the layer that judges EVERY tracked file of the language -- runs only when that gate
//! names it, so a repo that forgot it had a commit gate STRICTER than its full gate: necrohand's
//! `gate.sh --full` ran no Swift lint and 1274 standing `SwiftLint` violations were invisible to it
//! (NH.HEDGE2, 6e529bd, 2026-11-12), because a commit only ever sees the files it touches.
//!
//! THE RULE. For each row of `LAYERS` whose language the tree holds (tracked files of its
//! extension outside `GOH_EXCLUDE`; Swift also outside the root `.swiftlint.yml`'s `excluded`, which
//! the commit twin honours with `--force-exclude`), the full gate's text must run one of the row's
//! push layers. The full gate's text is what a push runs: `tools/gate.sh` and `.gatesrc`'s
//! `GOH_CI_STEPS` (`gates/push_gate.sh` runs those steps when the gate did not), comment lines
//! dropped, and the repo's OWN scripts those name, two levels deep (`gates/required_tools.py`'s
//! reach, GOH.REQTOOLS). A layer run under a repo-local name (a Makefile target, a script of its
//! own) is not goh's layer: its config and baseline reconciler are not the commit twin's, which is
//! how the two came apart.
//!
//! THE CLASS, not the instance: `tests::every_commit_twin_has_a_row` reads `gates/commit_gate.sh`'s
//! `twin_due` lines, so a staged twin added for a new language without a row here is red.
//!
//! WIRING: a structural step in every repo, both scopes (one tree read at either: the INDEX at a
//! commit, so the commit that adds the first `.swift` to a repo with no Swift layer is red there),
//! judged from `crate::grace::OVER_FROM` (2026-11-23). The estate's gaps on the day it landed are
//! seeded in `layer_parity_seed.json` (`crate::estate_seed`), one entry per `tools/gate.sh:<lang>`:
//! a seeded gap warns, an unseeded one is red, a seeded gap that is closed is stale.

use crate::estate_seed::{self, Kind, Report, Scope, Sites};
use crate::pathfilter::PathFilter;
use regex::Regex;
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[layer_parity]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "every language linted at commit is gated at push";

/// The seed of the estate's gaps on the day the check landed.
pub const KIND: Kind = Kind {
    file: "layer_parity_seed.json",
    unit: "missing layer",
    name: "layer-parity seed",
};

/// One language goh lints at commit, and the push layers that judge all of it.
pub struct Layer {
    /// The name in a finding's key (`tools/gate.sh:<lang>`).
    pub lang: &'static str,
    /// The tracked files that are this language.
    pub ext: &'static str,
    /// What the commit gate runs over the staged ones.
    pub commit: &'static str,
    /// A push layer that judges the whole tree: any one of these, read as a regex over the code.
    pub push: &'static [&'static str],
    /// The line a repo adds to its `tools/gate.sh --full`.
    pub fix: &'static str,
}

/// Every language the commit gate lints (`gates/commit_gate.sh`'s twins and the Rust staged step).
pub const LAYERS: [Layer; 3] = [
    Layer {
        lang: "rust",
        ext: ".rs",
        commit: "the structural step `rust source policies (staged)`",
        push: &[r"(?:^|[^\w.-])rust_gate\.sh(?:[^\w.-]|$)"],
        fix: "\"$GOH/gates/rust_gate.sh\" .",
    },
    Layer {
        lang: "python",
        ext: ".py",
        commit: "gates/py_staged.sh",
        push: &[
            r"(?:^|[^\w.-])py_gate\.sh(?:[^\w.-]|$)",
            r"(?:^|[^\w.-])py_staged\.sh[^\n:]*\s--all(?:[^\w-]|$)",
        ],
        fix: "\"$GOH/gates/py_gate.sh\" .  (a repo with no pytest suite: \"$GOH/gates/py_staged.sh\" --all .)",
    },
    Layer {
        lang: "swift",
        ext: ".swift",
        commit: "gates/swift_staged.sh",
        push: &[r"(?:^|[^\w.-])swift_gate\.sh(?:[^\w.-]|$)"],
        fix: "\"$GOH/gates/swift_gate.sh\" .",
    },
];

/// The repo's own scripts a gate follows, at most this many (`gates/required_tools.py`'s cap).
const MAX_SCRIPTS: usize = 64;

/// The seed key for `layer`'s gap: where the fix goes, and which language.
#[must_use]
pub fn key(layer: &Layer) -> String {
    format!("tools/gate.sh:{}", layer.lang)
}

/// `text` without its comment lines: a comment naming a layer is not a call.
fn code(text: &str) -> String {
    text.lines()
        .filter(|l| !l.trim_start().starts_with('#'))
        .collect::<Vec<_>>()
        .join("\n")
}

fn read(root: &Path, rel: &str, staged: bool) -> Option<String> {
    crate::gitutil::content_bytes(root, rel, staged)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

/// A repo-relative script a gate's text names (`tools/coverage_check.sh`, `./tools/x.py`), as
/// `gates/required_tools.py` reads one; `$GOH/gates/x.sh` is goh's, not the repo's.
static SCRIPT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^|[^\w$/.-])(?:\./)?((?:[\w-]+/)*[\w.-]+\.(?:sh|bash|py))(?:[^\w.-]|$)")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

/// Each layer's push patterns, compiled once, in `LAYERS` order.
static PUSH: LazyLock<Vec<Vec<Regex>>> = LazyLock::new(|| {
    LAYERS
        .iter()
        .map(|l| {
            l.push
                .iter()
                .map(|p| {
                    Regex::new(&format!("(?m){p}"))
                        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
                })
                .collect()
        })
        .collect()
});

/// What a push runs, as text: `tools/gate.sh`, `GOH_CI_STEPS`, and the repo's own scripts they
/// name, two levels deep, every one without its comment lines.
#[must_use]
pub fn push_text(root: &Path, tree: &BTreeSet<String>, staged: bool) -> String {
    let mut parts = Vec::new();
    if tree.contains("tools/gate.sh") {
        parts.push(code(
            &read(root, "tools/gate.sh", staged).unwrap_or_default(),
        ));
    }
    if tree.contains(".gatesrc") {
        let pairs =
            crate::gatesrc::parse_pairs(&read(root, ".gatesrc", staged).unwrap_or_default());
        if let Some(steps) = pairs.get("GOH_CI_STEPS") {
            parts.push(steps.clone());
        }
    }
    let mut seen = BTreeSet::new();
    let mut level = parts.clone();
    for _ in 0..2 {
        let mut found = Vec::new();
        for body in &level {
            for c in SCRIPT.captures_iter(body) {
                let rel = c[1].to_string();
                if seen.len() >= MAX_SCRIPTS || !tree.contains(&rel) || !seen.insert(rel.clone()) {
                    continue;
                }
                found.push(code(&read(root, &rel, staged).unwrap_or_default()));
            }
        }
        parts.extend(found.iter().cloned());
        level = found;
    }
    parts.join("\n")
}

/// The paths the root `.swiftlint.yml` lists under `excluded:`.
fn swiftlint_excluded(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut inside = false;
    for line in text.lines() {
        if !line.starts_with(' ') && !line.starts_with('-') && !line.trim().is_empty() {
            inside = line.trim_end() == "excluded:";
            continue;
        }
        if inside {
            if let Some(item) = line.trim().strip_prefix("- ") {
                let item = item.split(" #").next().unwrap_or("").trim();
                let item = item.trim_matches(|c| c == '"' || c == '\'');
                let item = item.trim_start_matches("./").trim_end_matches('/');
                if !item.is_empty() {
                    out.push(item.to_string());
                }
            }
        }
    }
    out
}

/// How many tracked files are each layer's language, after the repo's exemptions.
fn counts(
    root: &Path,
    tree: &BTreeSet<String>,
    exclude: Option<&PathFilter>,
    staged: bool,
) -> [usize; LAYERS.len()] {
    let swift_out = if tree.contains(".swiftlint.yml") {
        swiftlint_excluded(&read(root, ".swiftlint.yml", staged).unwrap_or_default())
    } else {
        Vec::new()
    };
    let mut n = [0; LAYERS.len()];
    for f in tree {
        if exclude.is_some_and(|x| x.is_match(f)) {
            continue;
        }
        for (i, layer) in LAYERS.iter().enumerate() {
            if !f.ends_with(layer.ext) {
                continue;
            }
            let swift_skip = layer.lang == "swift"
                && swift_out
                    .iter()
                    .any(|p| f == p || f.starts_with(&format!("{p}/")));
            if !swift_skip {
                n[i] += 1;
            }
        }
    }
    n
}

/// The gaps: `key -> its one listing line`, for each language the tree holds and no push layer runs.
#[must_use]
pub fn gaps(text: &str, counts: &[usize; LAYERS.len()]) -> Sites {
    let mut out = Sites::new();
    for ((layer, n), push) in LAYERS.iter().zip(counts).zip(PUSH.iter()) {
        if *n == 0 {
            continue;
        }
        if push.iter().any(|p| p.is_match(text)) {
            continue;
        }
        let verb = if *n == 1 { "is" } else { "are" };
        let line = format!(
            "    tools/gate.sh: no {} layer at push -- {} {verb} linted at commit ({}) and judged \
             whole by nothing; add to --full: {}",
            layer.lang,
            goh_tui::counted(*n, &format!("tracked {} file", layer.ext)),
            layer.commit,
            layer.fix
        );
        out.insert(key(layer), vec![line]);
    }
    out
}

/// The verdict for the repo at `root`: `(exit code, stdout, stderr)`. Nothing is judged within the
/// grace; what it will judge is shown.
fn judge(root: &Path, exclude: &str, staged: bool) -> (i32, String, String) {
    let store = estate_seed::store_dir();
    judge_at(
        root,
        exclude,
        staged,
        crate::grace::over(),
        store.as_deref(),
    )
}

/// `judge` with the grace decided and the private store named (`None`: no store).
fn judge_at(
    root: &Path,
    exclude: &str,
    staged: bool,
    over: bool,
    store: Option<&Path>,
) -> (i32, String, String) {
    use goh_tui::ERR;
    // Within the grace a commit's run stays as it was: listing the whole index for a gate that
    // judges nothing is a git spawn on every commit, and the push scope reads the same tree and
    // shows the gaps (GOH.GRACESHAPES; tests/test_goh_git_spawns.py is the ratchet).
    if !over && staged {
        return (0, String::new(), String::new());
    }
    let fail = |m: String| (2, String::new(), format!("{ERR} {TAG} {m}\n"));
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return fail(m),
    };
    let listed = if staged {
        crate::gitutil::index_files(root)
    } else {
        crate::gitutil::listed_files(root, false)
    };
    let tree: BTreeSet<String> = match listed {
        Ok(v) => v.into_iter().collect(),
        Err(m) => return fail(m),
    };
    let found = gaps(
        &push_text(root, &tree, staged),
        &counts(root, &tree, exclude.as_ref(), staged),
    );
    if !over {
        // Judged from the grace's end, the seed never loaded for a verdict (GOH.GRACEEND); the gaps
        // the day will find are the warning (GOH.GRACESHAPES).
        return crate::grace::ahead::warn_only((root, staged), (KIND, TAG), Ok(found));
    }
    let seed = match estate_seed::load_scoped(root, store, KIND, staged) {
        Ok(s) => s,
        Err(m) => return fail(m),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.LAYERPARITY",
        what: (
            "language linted at commit and never at push",
            "languages linted at commit and never at push",
        ),
        fix: "    The commit gate lints only what a commit touches; the push layer judges every file \
              of the language, under the same config.\n",
        ok: "every language linted at commit has its push layer".to_string(),
    };
    // Both scopes read the WHOLE tree, so both may call an entry stale, and a commit settles them all.
    let scope = Scope {
        commit: staged,
        full: true,
        read: Some(LAYERS.iter().map(key).collect()),
    };
    estate_seed::verdict_in(root, scope, KIND, &seed, &found, &report)
}

/// `goh layer-parity [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    let exclude = match crate::gatesrc::load(&root) {
        Ok(cfg) => cfg.exclude,
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            return 2;
        }
    };
    estate_seed::print(judge(&root, &exclude, staged))
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/layerparity.rs",
        judge(repo, &cfg.exclude, staged),
    )
}

#[cfg(test)]
#[path = "layerparity_tests.rs"]
mod tests;
