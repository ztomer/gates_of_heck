//! The scripts a release runs: every repo-local shell source a release script calls, transitively
//! (GOH.RELQUIETCALLEES).
//!
//! ztools' `bin/ab_test` sent `cargo test` to /dev/null inside a script `tools/release.sh` calls,
//! and `goh release-quiet`, which read only release-NAMED files, never saw it (ZTL.RELQUIET, caught
//! there only because the call site itself was flagged). DECIDED: follow the calls, transitively,
//! rather than ask each repo to declare its release-reachable scripts in `.gatesrc` -- a
//! declaration is a list somebody has to remember, and the call is already written down. A cycle
//! ends at a script already read.
//!
//! WHAT IS A CALL: the command a segment runs (after the words `crate::relquiet` skips, and `.` or
//! `source`) when it resolves to a TRACKED shell source: `./x.sh`, `tools/x.sh`, `bash x.sh`,
//! `"$ROOT/tools/x.sh"`, `"$(dirname "$0")/x.sh"`. A word holding `$` is cut after its last
//! variable part, and what is left is tried beside the calling script, then from the repo root.
//! NOT SEEN: a path assembled in a variable first (`s=tools/x.sh; "$s"`), and a non-shell callee
//! (a Python helper), which no shell rule reads.

use std::collections::{BTreeMap, BTreeSet};

/// The words before a command that are not the command.
const SKIP: [&str; 16] = [
    "if", "!", "then", "else", "elif", "do", "while", "until", "exec", "time", "env", "bash", "sh",
    "zsh", ".", "source",
];

/// `rel`'s directory, `""` at the root.
fn dir_of(rel: &str) -> &str {
    rel.rsplit_once('/').map_or("", |(d, _)| d)
}

/// `a/b/../c` -> `a/c`; `None` when it climbs out of the repo.
fn normalize(path: &str) -> Option<String> {
    let mut out: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                out.pop()?;
            }
            p => out.push(p),
        }
    }
    Some(out.join("/"))
}

/// The tracked shell source `cmd`, run from `from`, names, if any.
fn resolve(cmd: &str, from: &str, shell: &BTreeSet<String>) -> Option<String> {
    let tail = match cmd.rfind('$') {
        Some(at) => {
            let rest = &cmd[at..];
            // The variable part ends at the first `/` after it.
            &rest[rest.find('/')?..]
        }
        None => cmd,
    };
    let tail = tail.trim_start_matches('/');
    let is_sh = std::path::Path::new(tail)
        .extension()
        .is_some_and(|e| e.eq_ignore_ascii_case("sh"));
    if tail.is_empty() || (!cmd.contains('/') && !is_sh) {
        return None;
    }
    let beside = normalize(&format!("{}/{tail}", dir_of(from)));
    [beside, normalize(tail)]
        .into_iter()
        .flatten()
        .find(|c| shell.contains(c))
}

/// The repo-local shell sources `text` (the script `from`) calls, in line order, once each.
#[must_use]
pub fn callees(text: &str, from: &str, shell: &BTreeSet<String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for line in text.lines() {
        if line.trim_start().starts_with('#') {
            continue;
        }
        for seg in super::segments(line) {
            let words: Vec<&str> = seg
                .split_whitespace()
                .map(super::word)
                .filter(|w| !w.is_empty())
                .collect();
            let cmd = words.iter().find(|w| {
                let assign = w.split_once('=').is_some_and(|(k, _)| {
                    !k.is_empty() && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
                });
                !SKIP.contains(w) && !assign && !w.starts_with('-')
            });
            if let Some(found) = cmd.and_then(|c| resolve(c, from, shell)) {
                if found != from && !out.contains(&found) {
                    out.push(found);
                }
            }
        }
    }
    out
}

/// Every script the `release` scripts reach through calls, and the release script that first
/// reaches it: `callee -> release script`. A release script is never its own callee.
#[must_use]
pub fn reachable(release: &[String], texts: &BTreeMap<String, String>) -> BTreeMap<String, String> {
    let shell: BTreeSet<String> = texts.keys().cloned().collect();
    let mut seen: BTreeMap<String, String> = BTreeMap::new();
    for top in release {
        let mut todo = vec![top.clone()];
        while let Some(at) = todo.pop() {
            let Some(text) = texts.get(&at) else {
                continue;
            };
            for c in callees(text, &at, &shell) {
                if release.contains(&c) || seen.contains_key(&c) {
                    continue;
                }
                seen.insert(c.clone(), top.clone());
                todo.push(c);
            }
        }
    }
    seen
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(paths: &[&str]) -> BTreeSet<String> {
        paths.iter().map(ToString::to_string).collect()
    }

    #[test]
    fn each_call_shape_resolves_to_its_tracked_script() {
        let shell = set(&[
            "tools/release.sh",
            "bin/ab_test",
            "tools/lib.sh",
            "tools/x.sh",
        ]);
        let src = "./bin/ab_test --all\nbash tools/x.sh\n. \"$(dirname \"$0\")/lib.sh\"\n\
                   \"$ROOT/bin/ab_test\" again\nif ! \"${HERE}\"/x.sh; then exit 1; fi\n";
        assert_eq!(
            callees(src, "tools/release.sh", &shell),
            ["bin/ab_test", "tools/x.sh", "tools/lib.sh"]
        );
    }

    #[test]
    fn a_word_that_is_not_a_tracked_script_is_no_call() {
        let shell = set(&["tools/release.sh", "tools/x.sh"]);
        let src =
            "# ./tools/x.sh\ncargo test\nmake\ngit tag v1\n./tools/gone.sh\necho tools/x.sh\n";
        assert_eq!(
            callees(src, "tools/release.sh", &shell),
            Vec::<String>::new()
        );
    }

    #[test]
    fn calls_are_followed_transitively_and_a_cycle_ends() {
        let texts: BTreeMap<String, String> = [
            ("release.sh", "./a.sh\n"),
            ("a.sh", "./b.sh\n./release.sh\n"),
            ("b.sh", "./a.sh\n"),
            ("c.sh", "true\n"),
        ]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        let got = reachable(&["release.sh".to_string()], &texts);
        assert_eq!(got.keys().collect::<Vec<_>>(), ["a.sh", "b.sh"]);
        assert_eq!(got["b.sh"], "release.sh");
    }
}
