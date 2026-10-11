//! A release tag reachable from a pushed branch must reach the remote too
//! (`goh tag-version --remote`).
//!
//! `git push --follow-tags` sends ANNOTATED tags only. The servers fleet was
//! released by hand on 2026-10-10 with `git tag v1.10.3` (lightweight) and
//! `--follow-tags`: main moved, the tag stayed local, and both hosts described
//! themselves as `v1.10.2-2-g…` until the tags were pushed by name. The same
//! survey found `antiknob v0.13.0` and `divoom-control v0.35.0` -- annotated,
//! on commits origin had held for a month, never pushed.
//!
//! The rule is about the TAG, never the bump: a version may reach the branch
//! before its tag exists (monitor and antiknob tag after CI), but a local
//! `v<semver>` tag on a commit the branch carries is a release that was named
//! here and nowhere else. It must be in this push or already on the remote.
//!
//! A branch push is any line whose REMOTE ref is a branch: a pinned push
//! (`git push origin <sha>:refs/heads/main`, the `make push` of `ZoneWM`) hands the
//! hook a sha as its local ref. The remote is asked at the URL git hands the
//! hook, and named by its name in the fix line.

use std::collections::BTreeSet;
use std::process::Command;

use regex::Regex;

use crate::tagver::ZERO_SHA;

/// One pre-push line: `<local ref> <local sha> <remote ref> <remote sha>`.
pub struct RefLine {
    pub local_ref: String,
    pub local_sha: String,
    pub remote_ref: String,
}

/// The hook's stdin, one [`RefLine`] per line with at least a ref and a sha.
#[must_use]
pub fn parse(text: &str) -> Vec<RefLine> {
    crate::mdtext::splitlines(text)
        .into_iter()
        .filter_map(|l| {
            let mut parts = l.split_whitespace();
            let (local_ref, local_sha) = (parts.next()?.to_owned(), parts.next()?.to_owned());
            Some(RefLine {
                local_ref,
                local_sha,
                remote_ref: parts.next().unwrap_or_default().to_owned(),
            })
        })
        .collect()
}

fn lines(root: &str, args: &[&str]) -> Result<Vec<String>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("cannot run git: {e}"))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_owned());
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(str::to_owned)
        .collect())
}

/// The remote a push goes to: its name (for the fix line) and where to ask it.
pub struct Remote<'a> {
    pub name: &'a str,
    pub url: &'a str,
}

/// (findings, release tags judged). `Err` is a check that could not judge.
///
/// # Errors
///
/// git could not list the local tags, or the remote could not be asked.
pub fn audit(
    root: &str,
    refs: &[RefLine],
    remote: &Remote,
    tag_re: &Regex,
) -> Result<(Vec<String>, usize), String> {
    let pushed: BTreeSet<&str> = refs
        .iter()
        .filter(|r| r.local_sha != ZERO_SHA)
        .filter_map(|r| r.local_ref.strip_prefix("refs/tags/"))
        .collect();
    // Tag name -> the remote branch the push carries it on (for the fix line).
    let mut reachable: Vec<(String, String)> = Vec::new();
    for r in refs.iter().filter(|r| r.local_sha != ZERO_SHA) {
        let Some(branch) = r.remote_ref.strip_prefix("refs/heads/") else {
            continue;
        };
        for name in lines(root, &["tag", "--merged", &r.local_sha, "--list", "v*"])? {
            if tag_re.is_match(&format!("refs/tags/{name}"))
                && !reachable.iter().any(|(n, _)| *n == name)
            {
                reachable.push((name, branch.to_owned()));
            }
        }
    }
    let unsent: Vec<&(String, String)> = reachable
        .iter()
        .filter(|(n, _)| !pushed.contains(n.as_str()))
        .collect();
    if unsent.is_empty() {
        return Ok((Vec::new(), reachable.len()));
    }
    let Remote { name: remote, url } = *remote;
    let on_remote: BTreeSet<String> = lines(root, &["ls-remote", "--tags", "--refs", url])
        .map_err(|e| format!("cannot ask {remote} which tags it has ({e})"))?
        .iter()
        .filter_map(|l| l.split_once("refs/tags/").map(|(_, n)| n.to_owned()))
        .collect();
    let mut findings = Vec::new();
    for (name, branch) in unsent.into_iter().filter(|(n, _)| !on_remote.contains(n)) {
        let commit = lines(root, &["rev-parse", &format!("{name}^{{commit}}")])?
            .concat()
            .chars()
            .take(12)
            .collect::<String>();
        let kind = lines(root, &["cat-file", "-t", &format!("refs/tags/{name}")])?.concat();
        let why = if kind == "tag" {
            "annotated, but no push ever named it"
        } else {
            "lightweight: --follow-tags never sends one"
        };
        findings.push(format!(
            "refs/tags/{name} -> {commit}: a release tag on a commit {branch} carries, but neither this push nor {remote} has it ({why}) -- push it: git push {remote} {name} (or with the branch, in one push: git push {remote} {branch} {name})"
        ));
    }
    Ok((findings, reachable.len()))
}
