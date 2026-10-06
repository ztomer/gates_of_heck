//! A pushed release tag must name the version its own commit declares --
//! Rust port of `checks/check_tag_version.py` (Phase N1).
//!
//! `refs/tags/v1.36.0` pointing at a commit whose `VERSION` (or `Cargo.toml`,
//! or any `GOH_TAG_VERSION_SOURCES` source) says something else is a release
//! that reports itself differently from its name. Read AT THE COMMIT (`git
//! show <sha>:<path>`), never the working tree. A tag whose commit declares
//! no version at all is a finding, and a glob matching nothing says so.

use std::io::{IsTerminal, Read};
use std::path::Path;
use std::process::Command;

use regex::Regex;

const ZERO_SHA: &str = "0000000000000000000000000000000000000000";

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

fn audit(
    root: &str,
    refs: &[(String, String)],
    sources: &[(String, String)],
) -> Result<(Vec<String>, usize), String> {
    let strategies = crate::versrc::Strategies::new()?;
    let tag_re = Regex::new(r"^refs/tags/v(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)$")
        .map_err(|e| e.to_string())?;
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
fn run(root: Option<&str>, refs_file: Option<&str>, json: bool) -> (i32, Vec<(bool, String)>) {
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
    let refs: Vec<(String, String)> = crate::mdtext::splitlines(&text)
        .into_iter()
        .filter_map(|l| {
            let mut parts = l.split_whitespace();
            Some((parts.next()?.to_owned(), parts.next()?.to_owned()))
        })
        .collect();
    let (findings, examined) = match audit(&root, &refs, &sources) {
        Ok(v) => v,
        Err(e) => return fail(e),
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
        lines.push((true, format!("✗ --- {} finding(s): a tag you are pushing does not match what its own commit declares ---", findings.len())));
        return (1, lines);
    }
    if examined == 0 {
        return (
            0,
            vec![(
                false,
                "→ [tag_version] no refs/tags/v<semver> in this push — not applicable".to_owned(),
            )],
        );
    }
    (0, vec![(false, format!("✓ [tag_version] OK — {examined} pushed release tag(s) match the version declared at their own commit"))])
}

/// `goh tag-version [--root R] [--refs-file F] [--json]`.
#[must_use]
pub fn run_command(root: Option<&str>, refs_file: Option<&str>, json: bool) -> i32 {
    let (code, lines) = run(root, refs_file, json);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
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
