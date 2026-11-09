//! Where a hook-run shell source READS AN INDEX without binding the hook's.
//!
//! The commit hook drops `GIT_INDEX_FILE` and carries it as `GOH_HOOK_INDEX_FILE` (contract #12),
//! and `git commit -- path` / `commit -a` commit a TEMPORARY index only that variable names. So a
//! plain `git diff --cached` in a gate reads the repository's own index, not the tree being
//! committed: `.loop`'s tools/gate.sh never saw `queue.json` staged on a `git commit -- queue.json`
//! and skipped its queue gate on every tick (LOOP.COMMITSLOW, fixed in .loop 50ff65b).
//!
//! An index read is a `git` command whose subcommand reads the index: `diff`/`diff-index` with
//! `--cached`/`--staged`, `show :path`, `ls-files -s`/`--stage`, `write-tree`. It is BOUND when the
//! same file, earlier (on an earlier line or earlier on the same one), calls `goh_bind_hook_index`
//! or assigns `GIT_INDEX_FILE` itself (a private index the script chose). Earlier in the FILE, not
//! in the same subshell: a bind that ended with its `( ... )` still counts. The class this check
//! exists for is the gate that never binds at all; a scope-exact reading would need a parser.
//!
//! HOOK-RUN means reachable from what the commit hook runs: `tools/gate.sh` (`commit_gate.sh` runs it
//! `--staged`), `.githooks/pre-commit`, `hooks/pre-commit` and `gates/commit_gate.sh`, then every
//! tracked shell source one of those names by path -- root-relative (`lib/x.sh`), relative to the
//! naming file's directory, or `/name.sh` beside it (`"$(dirname "$0")/run_gates.sh"`). A release
//! script's `git diff --cached --quiet` is not hook-run and is not judged.

use super::scan::mask;

/// The files the commit hook runs first.
pub const ROOTS: [&str; 4] = [
    "tools/gate.sh",
    ".githooks/pre-commit",
    "hooks/pre-commit",
    "gates/commit_gate.sh",
];

/// One unbound read of an index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexRead {
    /// 1-based line the `git` is on.
    pub line: usize,
    /// The read, as the rule names it (`git diff --cached`).
    pub what: &'static str,
}

/// A token with the quotes at its edges dropped: `":queue.json"` is `:queue.json`.
fn bare(tok: &str) -> &str {
    tok.trim_matches(|c| c == '"' || c == '\'')
}

/// Global options that take the next word as their value.
const VALUED: [&str; 4] = ["-C", "-c", "--git-dir", "--work-tree"];

/// What the `git` invocation in `words` (starting after `git`) reads of the index, if anything.
fn reads_index(words: &[&str]) -> Option<&'static str> {
    let mut k = 0usize;
    while let Some(w) = words.get(k).map(|w| bare(w)) {
        if VALUED.contains(&w) {
            k += 2;
        } else if w.starts_with('-') {
            k += 1;
        } else {
            break;
        }
    }
    let sub = bare(words.get(k)?);
    let rest: Vec<&str> = words[k + 1..].iter().map(|w| bare(w)).collect();
    match sub {
        "diff" | "diff-index" if rest.iter().any(|w| matches!(*w, "--cached" | "--staged")) => {
            Some("git diff --cached")
        }
        "show" if rest.iter().any(|w| w.starts_with(':')) => Some("git show :path"),
        "ls-files"
            if rest.iter().any(|w| {
                *w == "--stage" || (w.starts_with('-') && !w.starts_with("--") && w.contains('s'))
            }) =>
        {
            Some("git ls-files --stage")
        }
        "write-tree" => Some("git write-tree"),
        _ => None,
    }
}

/// `w` is the `git` command word (`git`, `/usr/bin/git`, `command git` reads as `git`).
fn is_git(w: &str) -> bool {
    let w = bare(w);
    w == "git" || w.ends_with("/git")
}

/// The words of one simple command bind the hook index, or pick an index of their own.
fn binds(words: &[&str]) -> bool {
    words.iter().any(|w| {
        let w = bare(w);
        w == "goh_bind_hook_index" || w.starts_with("GIT_INDEX_FILE=")
    })
}

/// Every unbound index read in `src`, in order.
#[must_use]
pub fn unbound_reads(src: &str) -> Vec<IndexRead> {
    let code: String = mask(src, false).into_iter().collect();
    let mut out = Vec::new();
    let mut bound = false;
    let mut logical = String::new();
    let mut first = 1usize;
    for (n, raw) in code.split('\n').enumerate() {
        if logical.is_empty() {
            first = n + 1;
        }
        let (body, more) = raw
            .trim_end()
            .strip_suffix('\\')
            .map_or((raw, false), |b| (b, true));
        logical.push_str(body);
        logical.push(' ');
        if more {
            continue;
        }
        // A simple command ends at an operator, a subshell or substitution edge, or a backquote.
        for cmd in logical.split(|c| ";&|()`{}".contains(c)) {
            let words: Vec<&str> = cmd.split_whitespace().collect();
            if binds(&words) {
                bound = true;
            }
            if bound {
                continue;
            }
            if let Some(at) = words.iter().position(|w| is_git(w)) {
                if let Some(what) = reads_index(&words[at + 1..]) {
                    out.push(IndexRead { line: first, what });
                }
            }
        }
        logical.clear();
    }
    out
}

/// Does `text` (the comment-masked source at `from`) name the tracked shell source `to`? By its
/// root-relative path, its path below `from`'s directory, or `/name` when the two are siblings.
fn names(from: &str, text: &str, to: &str) -> bool {
    let dir_of = |p: &str| p.rsplit_once('/').map_or("", |(d, _)| d).to_string();
    let dir = dir_of(from);
    let mut spellings = vec![to.to_string()];
    if let Some(rel) = to
        .strip_prefix(&format!("{dir}/"))
        .filter(|_| !dir.is_empty())
    {
        spellings.push(rel.to_string());
    }
    if dir_of(to) == dir {
        spellings.push(format!("/{}", to.rsplit('/').next().unwrap_or(to)));
    }
    let word = |c: char| c.is_alphanumeric() || matches!(c, '_' | '-' | '.');
    spellings.iter().any(|s| {
        text.match_indices(s.as_str()).any(|(at, m)| {
            let before = text[..at].chars().next_back();
            let after = text[at + m.len()..].chars().next();
            (m.starts_with('/') || !before.is_some_and(word)) && !after.is_some_and(word)
        })
    })
}

/// The hook-run sources among `sources` (`(path, text)`): the roots and what they reach.
#[must_use]
pub fn hook_run(sources: &[(String, String)]) -> std::collections::BTreeSet<String> {
    let masked: Vec<String> = sources
        .iter()
        .map(|(_, t)| mask(t, false).into_iter().collect())
        .collect();
    let mut reached: std::collections::BTreeSet<String> = sources
        .iter()
        .filter(|(p, _)| ROOTS.contains(&p.as_str()))
        .map(|(p, _)| p.clone())
        .collect();
    let mut frontier: Vec<usize> = (0..sources.len())
        .filter(|i| reached.contains(&sources[*i].0))
        .collect();
    while let Some(i) = frontier.pop() {
        let (from, _) = &sources[i];
        for (j, (to, _)) in sources.iter().enumerate() {
            if !reached.contains(to) && names(from, &masked[i], to) {
                reached.insert(to.clone());
                frontier.push(j);
            }
        }
    }
    reached
}
