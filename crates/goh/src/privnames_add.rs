//! `goh private-names --add NAME`: the list grows only over a tree that is already clean of the
//! name (GOH.PRIVLISTORDER).
//!
//! The list is the private store's, uncommitted, and every public tree's gate reads it: a name
//! written there while the tree still names it turns every land red at once (2026-11-15: two names
//! added before the sweep that removed them had landed; every goh land went red and two lanes
//! stalled). So adding a name is a command that judges first: the name is refused, and nothing is
//! written, while the working tree OR the tree every land is judged against (`origin/HEAD`) still
//! names it -- a sweep on a lane is not a sweep on main until it lands. A refusal prints the lines,
//! so the sweep that comes first has its list.

use crate::privnames::{find, read_list, TAG};
use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;
use std::process::{Command, Stdio};

/// The base every land is judged against, when this checkout has one.
const BASE: &str = "origin/HEAD";

fn git(root: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("git {}: {e}", args.first().copied().unwrap_or("")))
}

fn nul_paths(stdout: &[u8]) -> Vec<String> {
    stdout
        .split(|b| *b == 0)
        .filter(|c| !c.is_empty())
        .map(|c| String::from_utf8_lossy(c).into_owned())
        .collect()
}

/// Every `where:path:line` (or `where:path` for a path) that names `name`, in the work tree
/// (`rev` `None`) or at `rev`.
///
/// # Errors
///
/// A git that answers and fails: an unreadable tree is not a clean one.
pub fn naming(root: &Path, rev: Option<&str>, name: &str) -> Result<Vec<String>, String> {
    let names = [name.to_owned()];
    let at = rev.unwrap_or("work tree");
    let mut args = vec!["grep", "-z", "-l", "-F", "-e", name];
    args.extend(rev);
    let out = git(root, &args)?;
    if !matches!(out.status.code(), Some(0 | 1)) {
        return Err(format!(
            "git grep at {at} failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    let mut found = Vec::new();
    for hit in nul_paths(&out.stdout) {
        let rel = rev
            .and_then(|r| hit.strip_prefix(&format!("{r}:")))
            .unwrap_or(&hit);
        let text = match rev {
            None => std::fs::read(root.join(rel)).unwrap_or_default(),
            Some(r) => git(root, &["show", &format!("{r}:{rel}")])?.stdout,
        };
        for (i, line) in String::from_utf8_lossy(&text).lines().enumerate() {
            if find(line, &names).is_some() {
                found.push(format!("    {at}: {rel}:{}", i + 1));
            }
        }
    }
    let listing = match rev {
        None => git(root, &["ls-files", "-z"])?,
        Some(r) => git(root, &["ls-tree", "-r", "-z", "--name-only", r])?,
    };
    for rel in nul_paths(&listing.stdout) {
        if find(&rel, &names).is_some() {
            found.push(format!("    {at}: {rel}: its path"));
        }
    }
    Ok(found)
}

/// Add `names` to the list at `list`, judged over the tree at `root`: `(exit code, report)`.
/// All or nothing: one refused name writes none.
#[must_use]
pub fn add(root: &Path, list: &Path, names: &[String]) -> (i32, String) {
    let have = read_list(list).unwrap_or_default();
    let base = git(
        root,
        &["rev-parse", "--verify", "-q", &format!("{BASE}^{{commit}}")],
    )
    .is_ok_and(|o| o.status.success());
    let revs: Vec<Option<&str>> = if base {
        vec![None, Some(BASE)]
    } else {
        vec![None]
    };
    let mut report = String::new();
    let mut refused = 0usize;
    let mut fresh = Vec::new();
    for name in names {
        let name = name.trim();
        if name.is_empty() || name.contains('#') {
            let _ = writeln!(
                report,
                "{ERR} {TAG} `{name}` is no list entry (empty, or a `#`)"
            );
            refused += 1;
            continue;
        }
        if have.iter().any(|h| h == name) {
            let _ = writeln!(report, "{OK} {TAG} `{name}` is already listed");
            continue;
        }
        let mut found = Vec::new();
        for rev in &revs {
            match naming(root, *rev, name) {
                Ok(f) => found.extend(f),
                Err(m) => return (2, format!("{report}{ERR} {TAG} {m}\n")),
            }
        }
        if found.is_empty() {
            fresh.push(name.to_owned());
        } else {
            refused += 1;
            let _ = writeln!(
                report,
                "{ERR} {TAG} `{name}` is still named here, so listing it now turns every land red; \
                 sweep these first, and add it once the sweep has landed:"
            );
            for f in &found {
                let _ = writeln!(report, "{f}");
            }
        }
    }
    if refused > 0 {
        let _ = writeln!(report, "{ERR} {TAG} nothing added to {}", list.display());
        return (1, report);
    }
    if fresh.is_empty() {
        return (0, report);
    }
    let mut text = std::fs::read_to_string(list).unwrap_or_else(|_| {
        "# private names: add with `goh private-names --add NAME`, which refuses a name a tree \
         still names\n"
            .to_owned()
    });
    if !text.is_empty() && !text.ends_with('\n') {
        text.push('\n');
    }
    for name in &fresh {
        let _ = writeln!(text, "{name}");
        let _ = writeln!(report, "{OK} {TAG} `{name}` added: no tree here names it");
    }
    let dir = list.parent().unwrap_or_else(|| Path::new("."));
    if let Err(e) = std::fs::create_dir_all(dir) {
        return (2, format!("{report}{ERR} {TAG} {}: {e}\n", dir.display()));
    }
    // Whole or not at all: a reader (every gate on the machine) never sees half a list.
    let next = list.with_extension(format!("new.{}", std::process::id()));
    if let Err(e) = std::fs::write(&next, text).and_then(|()| std::fs::rename(&next, list)) {
        let _ = std::fs::remove_file(&next);
        return (2, format!("{ERR} {TAG} {}: {e}\n", list.display()));
    }
    (0, report)
}

#[cfg(test)]
#[path = "privnames_add_tests.rs"]
mod tests;
