//! `goh private-names`: a public tree names no private repository (GOH.FINANCENAMES).
//!
//! `gates_of_heck` is public, and its comments, docs and test fixtures named the owner's private
//! repositories and their paths for months -- the provenance of a defect ("found in X") written as
//! the repository's name. A comment keeps its meaning with a neutral description ("a private
//! consumer", "a nested Cargo workspace"); this step refuses the name itself.
//!
//! THE LIST IS NOT IN THIS TREE. A list of private names committed to a public tree publishes
//! exactly what it guards. It is read from the owner's private store (`crate::estate_seed`'s
//! `store_dir()`), `private_names.txt`: one name or path per line, `#` comments. A name matches
//! case-sensitively at word edges (a letter, digit or `_` on either side is not an edge, so
//! `AcmeTender` is not `Acme`, and `Acme/ws` and `acme-like` both name `Acme`). A machine with
//! no list -- a GitHub runner -- cannot judge, and says so on a warning line naming where the list
//! lives: the owner's machine, which runs every push, is the judge of record.
//!
//! WIRING: a structural step behind `GOH_NO_PRIVATE_NAMES=on` in `.gatesrc`, which a public repo
//! sets. NOT SEEN: a name split across lines, or spelled with different case.

use goh_tui::{ERR, WARN};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

const TAG: &str = "[private_names]";
/// The list's file name in the private store.
pub const LIST: &str = "private_names.txt";
const LABEL: &str = "no private repository named in a public tree";

/// The names in the list at `path`: `None` when there is no list.
#[must_use]
pub fn read_list(path: &Path) -> Option<Vec<String>> {
    let text = std::fs::read_to_string(path).ok()?;
    Some(
        text.lines()
            .map(|l| l.split('#').next().unwrap_or("").trim())
            .filter(|l| !l.is_empty())
            .map(str::to_string)
            .collect(),
    )
}

const fn word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// The first listed name `line` holds at word edges.
#[must_use]
pub fn find<'a>(line: &str, names: &'a [String]) -> Option<&'a str> {
    names.iter().map(String::as_str).find(|name| {
        line.match_indices(name).any(|(at, _)| {
            let before = line[..at].chars().next_back();
            let after = line[at + name.len()..].chars().next();
            !before.is_some_and(word) && !after.is_some_and(word)
        })
    })
}

/// The findings over `files` in `root`: `path:line: name` lines.
fn findings(root: &Path, files: &[String], names: &[String], staged: bool) -> Vec<String> {
    let mut out = Vec::new();
    for rel in files {
        let Some(bytes) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        let text = String::from_utf8_lossy(&bytes);
        for (i, line) in text.lines().enumerate() {
            if let Some(name) = find(line, names) {
                out.push(format!("    {rel}:{}: names `{name}`", i + 1));
            }
        }
        // A path is a fact about the tree too: a fixture directory named after the repository.
        if let Some(name) = find(rel, names) {
            out.push(format!("    {rel}: its path names `{name}`"));
        }
    }
    out
}

/// The verdict for `root` with the list at `list`: `(exit code, report)`.
#[must_use]
pub fn judge(root: &Path, list: &Path, staged: bool) -> (i32, String) {
    let Some(names) = read_list(list) else {
        return (
            0,
            format!(
                "{WARN} {TAG} not judged: no private-name list at {} (the owner's machine holds \
                 it; a runner cannot judge)\n",
                list.display()
            ),
        );
    };
    let files = match crate::gitutil::listed_files(root, staged) {
        Ok(f) => f,
        Err(m) => return (2, format!("{ERR} {TAG} {m}\n")),
    };
    let found = findings(root, &files, &names, staged);
    if found.is_empty() {
        return (0, String::new());
    }
    let mut out = format!(
        "{ERR} {TAG} {} a private repository in a public tree:\n",
        goh_tui::counted_as(found.len(), "line that names", "lines that name")
    );
    for f in &found {
        let _ = writeln!(out, "{f}");
    }
    let _ = writeln!(
        out,
        "    Describe it instead (\"a private consumer\", \"a nested Cargo workspace\"); the list is \
         {} (GOH.FINANCENAMES)",
        list.display()
    );
    (1, out)
}

/// The list's path: the private store's `private_names.txt`.
#[must_use]
pub fn list_path() -> Option<PathBuf> {
    crate::estate_seed::store_dir().map(|d| d.join(LIST))
}

/// `goh private-names [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let Some(list) = list_path() else {
        goh_tui::eoutln!("{WARN} {TAG} not judged: no private store (no HOME)");
        return 0;
    };
    let (code, out) = judge(&root, &list, staged);
    goh_tui::eout(&out);
    code
}

/// The structural step, behind `GOH_NO_PRIVATE_NAMES=on`.
#[must_use]
pub fn step(repo: &Path, cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    if !crate::gatesrc::opt_in(cfg, "GOH_NO_PRIVATE_NAMES") {
        return None;
    }
    let label = if staged {
        format!("{LABEL} (staged)")
    } else {
        LABEL.to_string()
    };
    let start = crate::step_report::begin(&label);
    let Some(list) = list_path() else {
        goh_tui::eoutln!("{WARN} {TAG} not judged: no private store (no HOME)");
        crate::step_report::ok(&label, start);
        return None;
    };
    match judge(repo, &list, staged) {
        (0, warn) => {
            goh_tui::eout(&warn);
            crate::step_report::ok(&label, start);
            None
        }
        (code, out) => {
            let _ = crate::step_report::fail(
                &label,
                &crate::step_report::ported("crates/goh/src/privnames.rs"),
                &out,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| (*s).to_string()).collect()
    }

    #[test]
    fn a_name_matches_at_word_edges_only() {
        let n = names(&["Acme", "acme-ws"]);
        assert_eq!(find("found in Acme/ws at 1.2", &n), Some("Acme"));
        assert_eq!(find("a fixture `acme-ws-like`", &n), Some("acme-ws"));
        assert_eq!(find("AcmeTender and Acmes", &n), None);
        assert_eq!(find("acme in lower case", &n), None);
    }

    #[test]
    fn the_list_skips_comments_and_blank_lines() {
        let d = tempfile::tempdir().expect("tmp");
        let p = d.path().join(LIST);
        std::fs::write(&p, "# private\nAcme  # the metarepo\n\n  Widget\n").expect("write");
        assert_eq!(read_list(&p), Some(names(&["Acme", "Widget"])));
        assert_eq!(read_list(&d.path().join("absent")), None);
    }

    #[test]
    fn a_planted_name_is_red_and_no_list_is_a_named_warning() {
        let r = goh_testkit::repo_with(&[("a.rs", "// found in Acme (2026-01-01)\n")])
            .expect("fixture");
        let d = tempfile::tempdir().expect("tmp");
        let list = d.path().join(LIST);
        let (code, out) = judge(r.path(), &list, false);
        assert_eq!(code, 0);
        assert!(out.contains("not judged") && out.contains(LIST), "{out}");
        std::fs::write(&list, "Acme\n").expect("write");
        let (code, out) = judge(r.path(), &list, false);
        assert_eq!(code, 1, "{out}");
        assert!(out.contains("a.rs:1: names `Acme`"), "{out}");
        assert!(out.contains("1 line that names"), "{out}");
        std::fs::write(&list, "Other\n").expect("write");
        assert_eq!(judge(r.path(), &list, false), (0, String::new()));
    }
}
