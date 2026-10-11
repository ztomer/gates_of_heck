//! A pushed release tag must name the version its own commit declares --
//! Rust port of the retired `checks/check_tag_version.py` (Phase N1).
//!
//! `refs/tags/v1.36.0` pointing at a commit whose `VERSION` (or `Cargo.toml`,
//! or any `GOH_TAG_VERSION_SOURCES` source) says something else is a release
//! that reports itself differently from its name. Read AT THE COMMIT (`git
//! show <sha>:<path>`), never the working tree. A tag whose commit declares
//! no version at all is a finding, and a glob matching nothing says so.
//!
//! With `--remote`, a local release tag the pushed branches carry must be in
//! the push or already on that remote ([`crate::tagpush`]).

use std::io::{IsTerminal, Read};
use std::path::Path;
use std::process::Command;

use regex::Regex;

pub const ZERO_SHA: &str = "0000000000000000000000000000000000000000";

fn git(root: &str, args: &[&str]) -> Option<Vec<u8>> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .ok()?;
    out.status.success().then_some(out.stdout)
}

/// `fnmatch.fnmatch(name, pattern)`: `*` and `?` cross `/`, `[...]` classes.
fn fnmatch_regex(pattern: &str) -> Option<Regex> {
    let mut out = String::from("^(?s:");
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        match chars[i] {
            '*' => out.push_str(".*"),
            '?' => out.push('.'),
            '[' => {
                let close = chars
                    .iter()
                    .skip(i + 2)
                    .position(|c| *c == ']')
                    .map(|p| p + i + 2);
                match close {
                    Some(j) => {
                        let mut class: String = chars[i + 1..j].iter().collect();
                        if let Some(rest) = class.strip_prefix('!') {
                            class = format!("^{rest}");
                        }
                        out.push('[');
                        out.push_str(&class.replace('\\', "\\\\"));
                        out.push(']');
                        i = j;
                    }
                    None => out.push_str("\\["),
                }
            }
            c => out.push_str(&regex::escape(&c.to_string())),
        }
        i += 1;
    }
    out.push_str(")$");
    Regex::new(&out).ok()
}

fn norm(v: &str) -> &str {
    v.trim().trim_start_matches('v').trim()
}

/// `([(label, version)], [present-but-silent labels])` at `commit`.
fn declared_at(
    root: &str,
    commit: &str,
    sources: &[(String, String)],
    strategies: &crate::versrc::Strategies,
) -> (Vec<(String, String)>, Vec<String>) {
    let (mut found, mut present) = (Vec::new(), Vec::new());
    let mut tree: Option<Vec<String>> = None;
    for (kind, path) in sources {
        let targets: Vec<String> = if path.contains(['*', '?', '[']) {
            let names = tree.get_or_insert_with(|| {
                git(root, &["ls-tree", "-r", "--name-only", commit])
                    .map(|o| {
                        String::from_utf8_lossy(&o)
                            .lines()
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default()
            });
            fnmatch_regex(path).map_or_else(Vec::new, |rx| {
                names.iter().filter(|p| rx.is_match(p)).cloned().collect()
            })
        } else {
            vec![path.clone()]
        };
        if targets.is_empty() {
            present.push(format!("{path} [{kind}] (matched no path)"));
            continue;
        }
        for target in targets {
            let Some(blob) = git(root, &["show", &format!("{commit}:{target}")]) else {
                continue;
            };
            present.push(target.clone());
            for (table, version) in strategies
                .extract(kind, &String::from_utf8_lossy(&blob))
                .unwrap_or_default()
            {
                found.push((format!("{target} [{kind}] {table}"), version));
            }
        }
    }
    (found, present)
}

/// `refs/tags/v<semver>`: what a release tag looks like, for both halves.
fn release_tag_re() -> Result<Regex, String> {
    Regex::new(r"^refs/tags/v(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)$").map_err(|e| e.to_string())
}

fn audit(
    root: &str,
    refs: &[(String, String)],
    sources: &[(String, String)],
) -> Result<(Vec<String>, usize), String> {
    let strategies = crate::versrc::Strategies::new()?;
    let tag_re = release_tag_re()?;
    let (mut findings, mut examined) = (Vec::new(), 0usize);
    for (r, sha) in refs {
        let Some(expected) = tag_re
            .captures(r)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_owned())
        else {
            continue;
        };
        if sha == ZERO_SHA {
            continue;
        }
        examined += 1;
        let Some(commit) = git(
            root,
            &[
                "rev-parse",
                "--verify",
                "--quiet",
                &format!("{sha}^{{commit}}"),
            ],
        )
        .map(|o| String::from_utf8_lossy(&o).trim().to_owned()) else {
            findings.push(format!(
                "{r}: local sha {} does not resolve to a commit — cannot verify what this tag points at",
                sha.chars().take(12).collect::<String>()
            ));
            continue;
        };
        let (found, present) = declared_at(root, &commit, sources, &strategies);
        let short: String = commit.chars().take(12).collect();
        if found.is_empty() {
            let looked: Vec<String> = sources.iter().map(|(k, p)| format!("{p} [{k}]")).collect();
            let empty = if present.is_empty() {
                String::new()
            } else {
                format!("; present but empty: {}", present.join(", "))
            };
            findings.push(format!(
                "{r} -> {short}: NO version source declares a version at this commit (looked for {}{empty}) — the tag names a version nothing in the tree declares",
                looked.join(", ")
            ));
            continue;
        }
        let wrong: Vec<String> = found
            .iter()
            .filter(|(_, v)| norm(v) != norm(&expected))
            .map(|(label, v)| format!("{label} says {}", norm(v)))
            .collect();
        if !wrong.is_empty() {
            findings.push(format!(
                "{r} -> {short}: tag says {expected} but {} — anyone checking out this tag gets a build that reports itself differently",
                wrong.join(", ")
            ));
        }
    }
    Ok((findings, examined))
}

/// (exit code, [(to stderr?, line)]).
fn run(
    root: Option<&str>,
    refs_file: Option<&str>,
    json: bool,
    remote: Option<&str>,
) -> (i32, Vec<(bool, String)>) {
    let fail = |m: String| (2, vec![(true, format!("✗ [tag_version] {m}"))]);
    let Some(root) = root
        .map(str::to_owned)
        .or_else(crate::gitutil::repo_root)
        .filter(|r| Path::new(r).is_dir())
    else {
        return fail("not a git repository — pass --root".to_owned());
    };
    let spec = std::env::var("GOH_TAG_VERSION_SOURCES")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| crate::versrc::DEFAULT_SOURCES.join(" "));
    let sources = match crate::versrc::parse_sources(&spec) {
        Ok(s) => s,
        Err(e) => return fail(e),
    };
    if sources.is_empty() {
        return fail(
            "GOH_TAG_VERSION_SOURCES names no source — refusing to pass over nothing".to_owned(),
        );
    }
    let mut text = String::new();
    match refs_file.filter(|f| *f != "-") {
        None => {
            if !std::io::stdin().is_terminal() {
                let _ = std::io::stdin().read_to_string(&mut text);
            }
        }
        Some(f) => match std::fs::read(f) {
            Ok(b) => text = String::from_utf8_lossy(&b).into_owned(),
            Err(e) => return fail(format!("cannot read refs file {f}: {e}")),
        },
    }
    let lines = crate::tagpush::parse(&text);
    let refs: Vec<(String, String)> = lines
        .iter()
        .map(|l| (l.local_ref.clone(), l.local_sha.clone()))
        .collect();
    let (mut findings, examined) = match audit(&root, &refs, &sources) {
        Ok(v) => v,
        Err(e) => return fail(e),
    };
    let reached = match remote.filter(|r| !r.is_empty()) {
        None => None,
        Some(r) => {
            match release_tag_re().and_then(|re| crate::tagpush::audit(&root, &lines, r, &re)) {
                Ok((more, judged)) => {
                    findings.extend(more);
                    Some((r, judged))
                }
                Err(e) => return fail(e),
            }
        }
    };
    if json {
        let doc = serde_json::json!({ "findings": findings, "examined": examined });
        return (
            i32::from(!findings.is_empty()),
            vec![(false, crate::pyjson::dumps_indent2(&doc))],
        );
    }
    let mut lines: Vec<(bool, String)> = findings
        .iter()
        .map(|f| (true, format!("✗ [tag_version] {f}")))
        .collect();
    if !findings.is_empty() {
        lines.push((
            true,
            format!(
                "✗ --- {} finding(s): a release tag this push names or carries does not hold ---",
                findings.len()
            ),
        ));
        return (1, lines);
    }
    let reached_line = reached.map(|(r, judged)| {
        (
            false,
            format!("✓ [tag_version] {judged} local release tag(s) reachable from the pushed branch(es) are on {r} or in this push"),
        )
    });
    let head = if examined == 0 {
        (
            false,
            "→ [tag_version] no refs/tags/v<semver> in this push — not applicable".to_owned(),
        )
    } else {
        (false, format!("✓ [tag_version] OK — {examined} pushed release tag(s) match the version declared at their own commit"))
    };
    (0, std::iter::once(head).chain(reached_line).collect())
}

/// `goh tag-version [--root R] [--refs-file F] [--remote NAME] [--json]`.
#[must_use]
pub fn run_command(
    root: Option<&str>,
    refs_file: Option<&str>,
    json: bool,
    remote: Option<&str>,
) -> i32 {
    let (code, lines) = run(root, refs_file, json, remote);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// `goh tag-version`: the audit, or `--extract KIND`.
#[must_use]
pub fn command(a: &crate::cli::TagVersionArgs) -> i32 {
    a.extract.as_deref().map_or_else(
        || {
            run_command(
                a.root.as_deref(),
                a.refs_file.as_deref(),
                a.json,
                a.remote.as_deref(),
            )
        },
        extract_command,
    )
}

/// `--extract KIND`: what one strategy reads from stdin, `[[table, version], ...]`.
#[must_use]
pub fn extract_command(kind: &str) -> i32 {
    let mut text = String::new();
    if std::io::stdin().read_to_string(&mut text).is_err() {
        eprintln!("✗ [tag_version] stdin is not UTF-8 text");
        return 2;
    }
    let found = crate::versrc::Strategies::new()
        .ok()
        .and_then(|s| s.extract(kind, &text));
    let Some(found) = found else {
        eprintln!("✗ [tag_version] no strategy {kind:?}");
        return 2;
    };
    let rows: Vec<serde_json::Value> = found
        .into_iter()
        .map(|(t, v)| serde_json::json!([t, v]))
        .collect();
    println!("{}", crate::pyjson::dumps(&serde_json::Value::Array(rows)));
    0
}
