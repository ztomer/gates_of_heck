//! A temp, lock or staging path minted INSIDE a work tree (`goh run-paths`, GOH.CLASS.CHECKOUT).
//!
//! goh wrote its own scratch into the checkouts it serves -- `bin/.build.lock`,
//! `bin/goh.<pid>.staging`, the `build/release-vX` worktree, `<repo>/.goh-tree.lock` -- and ran
//! `git write-tree` on the shared index, taking `index.lock` while a commit there held it
//! (GOH.REFRESHINDEXLOCK). Each read as "N uncommitted", failed a ship, or refused another writer.
//! Every such path now comes from ONE helper rooted outside the work tree (`lib/run_path.sh`,
//! `lib/run_path.py`, `crate::run_path`), and this step refuses a new one written by hand.
//!
//! WHAT IS JUDGED, line by line in the shell, Python and Rust sources (comment lines, test files
//! and a Rust file's `#[cfg(test)]` tail are not read; the three helper files are the one place):
//!
//! * a TEMP-SHAPED name (`.lock`, `.staging`, `.tmp`, `XXXXXX`, `mktemp`, `mkstemp`, `mkdtemp`,
//!   `NamedTemporaryFile`, `TemporaryDirectory`; a package manager's lockfile and a `.lock()` call
//!   are not) on a line that roots it at a WORK TREE: a shell `$PROJECT_ROOT`, `$repo`, `$root`,
//!   `$top`, `$main`, `$wt`, `$PWD`, `$(pwd)` or a positional `$1`..`$9` followed by `/` or `.`;
//!   a Python `repo /`, `root /`, `Path.cwd()`, `os.getcwd()`, `dir=repo`, `os.path.join(repo`;
//!   a Rust `repo.join(`, `root.join(`;
//! * a path in such a root's `build/` (`$main/build/...`, `repo / "build"`, `repo.join("build"`);
//! * `git write-tree` (or a `"write-tree"` argv word): it writes the shared index under its lock.
//!
//! WHAT IS NOT READ: a path built in a variable on an earlier line and used here (`$lock`), a root
//! under another name, and any write a non-shell, non-Python, non-Rust file makes. The runtime half
//! of the class is `tests/test_entry_points_leave_no_strays.py`, which runs each entry point and
//! fails on any file left in the work tree.
//!
//! ESCAPE: `run-path-ok: <reason>` on the line or the line above (place.sh's cross-device stage).
//! WIRING: opt-in, `GOH_RUN_PATHS=on` (goh's own `.gatesrc`). The sites standing when it landed
//! are seeded in `run_paths_seed.json` (`crate::estate_seed`): seeded files warn, anything else,
//! and a stale entry, is red. It landed with an empty seed: every known site moved first.

use crate::estate_seed::{self, Kind, Report, Sites};
use regex::Regex;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[run_paths]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no temp, lock or stage path in a work tree";
const MARK: &str = "run-path-ok:";

/// The seed of the sites standing on the day the check landed.
pub const KIND: Kind = Kind {
    file: "run_paths_seed.json",
    unit: "work-tree temp path",
    name: "run-paths seed",
};

/// The helper itself: the one place these paths and the private-index write-tree are minted.
const HELPERS: [&str; 3] = [
    "lib/run_path.sh",
    "lib/run_path.py",
    "crates/goh/src/run_path.rs",
];

const FIX: &str = "    goh's temp, lock and staging paths come from ONE helper rooted outside the work tree, so\n    \
     a killed or running goh leaves nothing a claim card reads as uncommitted, and nothing takes\n    \
     the shared index.lock:\n      \
     shell   . lib/run_path.sh; d=\"$(goh_run_dir \"$tree\")\"; tree=\"$(goh_index_tree)\"\n      \
     python  run_path.run_dir(tree) / ...; run_path.index_tree(repo)\n      \
     rust    crate::run_path::dir_for(tree); crate::run_path::index_tree(None)\n    \
     A path that must stay (another filesystem, say) carries `run-path-ok: <reason>`.\n";

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Lang {
    Shell,
    Python,
    Rust,
}

const ROOTS: &str =
    "PROJECT_ROOT|REPO_ROOT|ROOT|repo|root|top|TOP|main|wt|PWD|REPO|toplevel|worktree";

static LOCKFILES: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:Cargo|flake|uv|poetry|Gemfile|yarn|pnpm-lock|package-lock|Podfile|composer|mix|bun|Pipfile|deno)\.lock")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static TEMP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"\.lock(?:[^\w(]|$)|\.staging\b|\.tmp\b|XXXXXX|\bmktemp\b|mkstemp|mkdtemp|NamedTemporaryFile|TemporaryDirectory",
    )
    .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static SH_ROOT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"\$\{{?(?:{ROOTS}|[1-9])\}}?[/.]|\$\(pwd\)/"))
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static PY_ROOT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r#"\b(?:{ROOTS})\s*/\s*f?["']|Path\.cwd\(\)|os\.getcwd\(\)|\bdir\s*=\s*(?:str\()?(?:{ROOTS})\b|os\.path\.join\((?:{ROOTS})\b"#
    ))
    .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static RS_ROOT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"\b(?:{ROOTS})\.join\("))
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static SH_BUILD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r"\$\{{?(?:{ROOTS})\}}?/build/"))
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static PY_BUILD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r#"\b(?:{ROOTS})\s*/\s*["']build["/']"#))
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static RS_BUILD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r#"\b(?:{ROOTS})\.join\("build["/]"#))
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static WRITE_TREE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"\bgit(?:\s+-C\s+\S+)*\s+write-tree\b|"write-tree"\s*[,\]]"#)
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

fn lang_of(rel: &str, text: &str) -> Option<Lang> {
    let p = Path::new(rel);
    match p.extension().and_then(|e| e.to_str()) {
        Some("rs") => return Some(Lang::Rust),
        Some("py") => return Some(Lang::Python),
        Some("sh" | "bash") => return Some(Lang::Shell),
        _ => {}
    }
    let first = text.lines().next().unwrap_or("");
    if !first.starts_with("#!") {
        return None;
    }
    if first.contains("python") {
        Some(Lang::Python)
    } else if first.contains("bash") || first.contains("/sh") {
        Some(Lang::Shell)
    } else {
        None
    }
}

fn is_test(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    rel.split('/').any(|c| c == "tests")
        || name == "tests.rs"
        || name.ends_with("_tests.rs")
        || name.starts_with("test_")
        || name == "conftest.py"
}

/// Why `line` mints a work-tree temp path, if it does.
fn finding(lang: Lang, line: &str) -> Option<&'static str> {
    let clean = LOCKFILES.replace_all(line, "");
    let (root, build) = match lang {
        Lang::Shell => (&*SH_ROOT, &*SH_BUILD),
        Lang::Python => (&*PY_ROOT, &*PY_BUILD),
        Lang::Rust => (&*RS_ROOT, &*RS_BUILD),
    };
    if TEMP.is_match(&clean) && root.is_match(&clean) {
        Some("a temp, lock or stage path rooted at a work tree")
    } else if build.is_match(line) {
        Some("a scratch path in a work tree's build/")
    } else if WRITE_TREE.is_match(line) {
        Some("a write-tree on the shared index (it takes index.lock)")
    } else {
        None
    }
}

/// An escape names its reason: `run-path-ok:` followed by words.
fn escaped(line: &str) -> bool {
    line.split_once(MARK)
        .is_some_and(|(_, why)| why.trim().len() >= 3)
}

/// Each finding in one source: `(line number, why, line)`.
fn scan(lang: Lang, text: &str) -> Vec<(usize, &'static str, String)> {
    let comment = if lang == Lang::Rust { "//" } else { "#" };
    let mut out = Vec::new();
    let mut prev = "";
    for (n, line) in text.lines().enumerate() {
        let t = line.trim_start();
        if lang == Lang::Rust && t.starts_with("#[cfg(test)]") {
            break;
        }
        let skip = t.starts_with(comment) || escaped(line) || escaped(prev);
        prev = line;
        if skip {
            continue;
        }
        if let Some(why) = finding(lang, line) {
            out.push((n + 1, why, line.to_string()));
        }
    }
    out
}

/// The sites among `files`.
fn sites(
    root: &Path,
    files: &[String],
    exclude: &str,
    staged: bool,
) -> Result<(Sites, usize), String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let mut out = Sites::new();
    let mut read = 0;
    for rel in files {
        if HELPERS.contains(&rel.as_str())
            || is_test(rel)
            || exclude.as_ref().is_some_and(|x| x.is_match(rel))
        {
            continue;
        }
        let Some(text) = crate::gitutil::content_bytes(root, rel, staged)
            .and_then(|b| String::from_utf8(b).ok())
        else {
            continue;
        };
        let Some(lang) = lang_of(rel, &text) else {
            continue;
        };
        read += 1;
        let found: Vec<String> = scan(lang, &text)
            .into_iter()
            .map(|(n, why, line)| format!("    {rel}:{n}: {}  -- {why}", estate_seed::clip(&line)))
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    crate::subject::record(&crate::subject::RUN_PATHS, read);
    Ok((out, read))
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let (found, read) = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.CLASS.CHECKOUT",
        what: (
            "temp, lock or stage path minted in a work tree",
            "temp, lock or stage paths minted in a work tree",
        ),
        fix: FIX,
        ok: format!(
            "every temp, lock and stage path comes from the run-path helper ({read} sources read)"
        ),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh run-paths [--staged] [--exclude RE]`.
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

/// The structural step (opt-in, `GOH_RUN_PATHS`).
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    if !crate::gatesrc::opt_in(cfg, "GOH_RUN_PATHS") {
        return None;
    }
    estate_seed::step(
        LABEL,
        "crates/goh/src/runpaths.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests {
    use super::{escaped, finding, is_test, lang_of, scan, Lang};

    #[test]
    fn each_site_the_class_was_found_at_is_a_finding() {
        // The four sites of the item and their two siblings, as they stood on main (da7446f).
        for (lang, line) in [
            (Lang::Shell, r#"LOCK="${PROJECT_ROOT}/bin/.build.lock""#),
            (
                Lang::Shell,
                r#"    local stage="$2.${BASHPID:-$$}.staging""#,
            ),
            (Lang::Shell, r#"wt="$main/build/release-$TAG""#),
            (Lang::Shell, "    git write-tree 2>/dev/null"),
            (Lang::Shell, r#"    lock="$repo/.goh-tree.lock""#),
            (Lang::Rust, r#"    let t = git(&["write-tree"])?;"#),
            (Lang::Python, r#"    lock = repo / ".goh.lock""#),
            (Lang::Python, "    fd, tmp = tempfile.mkstemp(dir=root)"),
            (Lang::Rust, r#"    let tmp = repo.join(".x.tmp");"#),
            (Lang::Python, r#"    out = root / "build" / "x""#),
        ] {
            assert!(finding(lang, line).is_some(), "{lang:?}: {line}");
        }
    }

    #[test]
    fn a_path_outside_every_tree_and_a_lockfile_are_not() {
        for (lang, line) in [
            (
                Lang::Shell,
                r#"notes="$(mktemp "${TMPDIR:-/tmp}/goh-notes.XXXXXX")""#,
            ),
            (Lang::Shell, r#"lock="$(goh_run_dir "$repo")/tree.lock""#),
            (Lang::Shell, r#"git -C "$repo" add Cargo.lock"#),
            (Lang::Python, r#"lock = repo / "Cargo.lock""#),
            (Lang::Python, "tmp = tempfile.mkdtemp(prefix='goh-x.')"),
            (Lang::Rust, "let map = cache().lock().ok()?;"),
            (Lang::Rust, r#"let lock = root.join("flake.lock");"#),
            (Lang::Rust, r#"let tmp = dir.join(format!(".tmp.{}", id));"#),
        ] {
            assert_eq!(finding(lang, line), None, "{lang:?}: {line}");
        }
    }

    #[test]
    fn comments_escapes_and_a_rust_test_tail_are_not_read() {
        let sh = "# LOCK=\"$repo/.x.lock\"\nx=\"$1.$$.staging\"  # run-path-ok: another filesystem\n\
                  # run-path-ok: the line below is the reason\ny=\"$repo/.y.lock\"\nz=\"$repo/.z.lock\"\n";
        let hits: Vec<usize> = scan(Lang::Shell, sh).into_iter().map(|h| h.0).collect();
        assert_eq!(hits, vec![5]);
        assert!(!escaped("x # run-path-ok:"), "an escape names its reason");
        let rs = "fn a() {}\n#[cfg(test)]\nmod tests { let t = repo.join(\".t.lock\"); }\n";
        assert_eq!(scan(Lang::Rust, rs), Vec::new());
    }

    #[test]
    fn the_language_comes_from_the_suffix_or_the_shebang_and_tests_are_skipped() {
        assert_eq!(lang_of("a/b.rs", ""), Some(Lang::Rust));
        assert_eq!(lang_of("tool", "#!/usr/bin/env bash\n"), Some(Lang::Shell));
        assert_eq!(
            lang_of("tool", "#!/usr/bin/env python3\n"),
            Some(Lang::Python)
        );
        assert_eq!(lang_of("README", "text\n"), None);
        for t in [
            "tests/x.py",
            "crates/goh/src/a/tests.rs",
            "src/b_tests.rs",
            "x/test_a.py",
        ] {
            assert!(is_test(t), "{t}");
        }
        assert!(!is_test("lib/place.sh"));
    }
}
