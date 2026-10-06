//! A committed `Cargo.lock` must agree with its manifests -- Rust port of
//! `checks/check_lock_version.py` and `checks/_cargo_toml.py` (Phase N1).
//!
//! `app_updates`, 2026-10-01: the release bumped `[workspace.package] version`
//! and shipped a lockfile one version behind; any `cargo build` heals the
//! tree, so the defect lives only in the COMMIT. Arm 1 compares every
//! workspace member's version (inheritance resolved) with its lock entry;
//! arm 2 compares the release version a source OUTSIDE the manifests declares
//! (`GOH_TAG_VERSION_SOURCES`) with what the members declare and lock.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use regex::Regex;

use crate::mdtext::splitlines;

/// `pathlib.Path.glob(pattern)` under `root`, sorted: hidden entries
/// included, `**` any number of segments (a trailing one matches files too).
fn glob(root: &Path, pattern: &str) -> Vec<PathBuf> {
    fn matches(pat: &[char], name: &[char]) -> bool {
        match (pat.first(), name.first()) {
            (None, None) => true,
            (Some('*'), _) => {
                matches(&pat[1..], name) || (!name.is_empty() && matches(pat, &name[1..]))
            }
            (Some('?'), Some(_)) => matches(&pat[1..], &name[1..]),
            (Some('['), Some(c)) => {
                let Some(close) = pat.iter().skip(2).position(|p| *p == ']').map(|p| p + 2) else {
                    return *c == '[' && matches(&pat[1..], &name[1..]);
                };
                let class = &pat[1..close];
                let (neg, class) = match class.first() {
                    Some('!') => (true, &class[1..]),
                    _ => (false, class),
                };
                let mut hit = false;
                let mut i = 0;
                while i < class.len() {
                    if i + 2 < class.len() && class[i + 1] == '-' {
                        hit |= class[i] <= *c && *c <= class[i + 2];
                        i += 3;
                    } else {
                        hit |= class[i] == *c;
                        i += 1;
                    }
                }
                hit != neg && matches(&pat[close + 1..], &name[1..])
            }
            (Some(p), Some(c)) => p == c && matches(&pat[1..], &name[1..]),
            _ => false,
        }
    }
    fn entries(dir: &Path) -> Vec<PathBuf> {
        let mut v: Vec<PathBuf> = std::fs::read_dir(dir)
            .map(|rd| rd.filter_map(Result::ok).map(|e| e.path()).collect())
            .unwrap_or_default();
        v.sort();
        v
    }
    fn walk(dir: &Path, comps: &[&str], out: &mut BTreeSet<PathBuf>) {
        let Some((first, rest)) = comps.split_first() else {
            out.insert(dir.to_path_buf());
            return;
        };
        if *first == "**" {
            walk(dir, rest, out);
            for e in entries(dir) {
                if e.is_dir() {
                    walk(&e, comps, out);
                } else if rest.is_empty() {
                    out.insert(e);
                }
            }
            return;
        }
        let pat: Vec<char> = first.chars().collect();
        for e in entries(dir) {
            let name: Vec<char> = e
                .file_name()
                .map(|n| n.to_string_lossy().chars().collect())
                .unwrap_or_default();
            if matches(&pat, &name) && (rest.is_empty() || e.is_dir()) {
                walk(&e, rest, out);
            }
        }
    }
    let comps: Vec<&str> = pattern
        .split('/')
        .filter(|c| !c.is_empty() && *c != ".")
        .collect();
    let mut out = BTreeSet::new();
    walk(root, &comps, &mut out);
    out.into_iter().collect()
}

struct Toml {
    lock_table: Regex,
    lock_field: Regex,
    table: Regex,
    key: Regex,
    inherit: Regex,
    literal: Regex,
    members: Regex,
    quoted: Regex,
}

impl Toml {
    fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("cargo toml pattern {p:?}: {e}"));
        Ok(Self {
            lock_table: rx(r"^\[\[package\]\]\s*$")?,
            lock_field: rx(r#"^(\w+)\s*=\s*"(.*)"\s*$"#)?,
            table: rx(r"^\s*\[([A-Za-z0-9_.\-]+)\]")?,
            key: rx(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*$")?,
            inherit: rx(r"^version\s*\.\s*workspace\s*=\s*true\s*$")?,
            literal: rx(r#"^version\s*=\s*"([^"]+)"\s*$"#)?,
            members: rx(r"(?ms)^\s*members\s*=\s*\[([^\]]*)\]")?,
            quoted: rx(r#""([^"]+)""#)?,
        })
    }

    fn section(&self, text: &str, name: &str) -> String {
        let mut out = Vec::new();
        let mut inside = false;
        for raw in splitlines(text) {
            if let Some(h) = self.table.captures(raw).and_then(|c| c.get(1)) {
                inside = h.as_str() == name;
                if inside {
                    continue;
                }
            }
            if inside {
                out.push(raw);
            }
        }
        out.join("\n")
    }

    /// `{name: version}` for every `[[package]]` (first entry of a name wins).
    fn parse_lock(&self, text: &str) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        let mut current: Option<BTreeMap<String, String>> = None;
        for line in splitlines(text) {
            if self.lock_table.is_match(line) {
                current = Some(BTreeMap::new());
                continue;
            }
            let Some(cur) = current.as_mut() else {
                continue;
            };
            if let Some(c) = self.lock_field.captures(line) {
                cur.insert(c[1].to_owned(), c[2].to_owned());
                if let (Some(n), Some(v)) = (cur.get("name"), cur.get("version")) {
                    out.entry(n.clone()).or_insert_with(|| v.clone());
                }
                continue;
            }
            if line.starts_with('[') {
                current = None;
            }
        }
        out
    }

    /// `(name, literal version)` of the `[package]` table; `None` version is
    /// inherited (or absent).
    fn parse_package(&self, text: &str) -> (Option<String>, Option<String>) {
        let (mut name, mut version) = (None, None);
        let mut table = String::new();
        for raw in splitlines(text) {
            if let Some(h) = self.table.captures(raw).and_then(|c| c.get(1)) {
                h.as_str().clone_into(&mut table);
                if table != "package" {
                    return (name, version);
                }
                continue;
            }
            if table != "package" {
                continue;
            }
            let Some(k) = self.key.captures(raw) else {
                continue;
            };
            if &k[1] == "name" && name.is_none() {
                name = Some(k[2].trim_matches('"').to_owned());
            } else if &k[1] == "version" && version.is_none() {
                version = if self.inherit.is_match(raw) {
                    None
                } else {
                    self.literal.captures(raw).map(|c| c[1].to_owned())
                };
            }
        }
        (name, version)
    }

    fn parse_workspace(&self, text: &str) -> (Option<String>, Vec<String>) {
        let ws = self.section(text, "workspace");
        let members = self.members.captures(&ws).map_or_else(Vec::new, |c| {
            self.quoted
                .captures_iter(&c[1])
                .map(|q| q[1].to_owned())
                .collect()
        });
        let version = splitlines(&self.section(text, "workspace.package"))
            .into_iter()
            .find_map(|l| self.literal.captures(l).map(|c| c[1].to_owned()));
        (version, members)
    }
}

fn read(path: &Path) -> String {
    std::fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
        .unwrap_or_default()
}

fn rel(root: &Path, p: &Path) -> String {
    p.strip_prefix(root)
        .unwrap_or(p)
        .to_string_lossy()
        .into_owned()
}

fn member_manifests(root: &Path, members: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for pattern in members {
        if pattern.contains(['*', '?', '[']) {
            out.extend(glob(root, pattern).into_iter().filter(|p| p.is_dir()));
        } else if root.join(pattern).is_dir() {
            out.push(root.join(pattern));
        }
    }
    out.into_iter()
        .map(|p| p.join("Cargo.toml"))
        .filter(|p| p.is_file())
        .collect()
}

/// `[(label, version)]` the release sources declare, from the working tree.
fn release_version(root: &Path, spec: &str) -> Result<Vec<(String, String)>, String> {
    let strategies = crate::versrc::Strategies::new()?;
    let mut found = Vec::new();
    for (kind, path) in crate::versrc::parse_sources(spec)? {
        let files: Vec<(String, PathBuf)> = if path.contains(['*', '?', '[']) {
            glob(root, &path)
                .into_iter()
                .filter(|p| p.is_file())
                .map(|p| (rel(root, &p), p))
                .collect()
        } else {
            let p = root.join(&path);
            if p.is_file() {
                vec![(path.clone(), p)]
            } else {
                Vec::new()
            }
        };
        for (label, p) in files {
            for (_, v) in strategies.extract(&kind, &read(&p)).unwrap_or_default() {
                found.push((label.clone(), v));
            }
        }
    }
    Ok(found)
}

/// `(findings, examined, notes)`.
fn audit(root: &Path, spec: &str) -> Result<(Vec<String>, usize, Vec<String>), String> {
    let toml = Toml::new()?;
    let (mut findings, mut notes) = (Vec::new(), Vec::new());
    let lock = root.join("Cargo.lock");
    if !lock.is_file() {
        notes.push("no Cargo.lock in this repository — nothing to compare".to_owned());
        return Ok((findings, 0, notes));
    }
    let root_manifest = root.join("Cargo.toml");
    if !root_manifest.is_file() {
        notes.push("no Cargo.toml beside Cargo.lock — nothing to compare".to_owned());
        return Ok((findings, 0, notes));
    }
    let locked = toml.parse_lock(&read(&lock));
    let root_text = read(&root_manifest);
    let (workspace_version, members) = toml.parse_workspace(&root_text);
    let mut manifests = member_manifests(root, &members);
    let (root_name, root_version) = toml.parse_package(&root_text);
    if root_name.is_some() && (root_version.is_some() || workspace_version.is_some()) {
        manifests.insert(0, root_manifest);
    }
    if manifests.is_empty() {
        notes.push(
            "this manifest declares no package and no workspace members — nothing to compare"
                .to_owned(),
        );
        return Ok((findings, 0, notes));
    }
    let mut resolved: BTreeMap<String, String> = BTreeMap::new();
    let mut stale: BTreeSet<String> = BTreeSet::new();
    let mut compared = 0usize;
    for manifest in &manifests {
        let r = rel(root, manifest);
        let (name, version) = toml.parse_package(&read(manifest));
        let Some(name) = name.filter(|n| !n.is_empty()) else {
            notes.push(format!("{r}: no [package] name — not compared"));
            continue;
        };
        let Some(version) = version.or_else(|| workspace_version.clone()) else {
            notes.push(format!(
                "{r}: inherits a version and no [workspace.package] declares one — not compared"
            ));
            continue;
        };
        resolved.insert(name.clone(), version.clone());
        compared += 1;
        match locked.get(&name) {
            None => findings.push(format!(
                "{r}: declares {name} {version} but Cargo.lock has no entry for {name} — the lockfile is missing a workspace member"
            )),
            Some(lv) if *lv != version => {
                stale.insert(name.clone());
                findings.push(format!(
                    "{r}: {name} declares version {version} but Cargo.lock says {lv} — the lockfile is stale; cargo will rewrite it on the next build, and this commit is what gets published"
                ));
            }
            Some(_) => {}
        }
    }
    let declared = release_version(root, spec)?;
    let numbers: BTreeSet<&str> = declared.iter().map(|(_, v)| v.as_str()).collect();
    if numbers.len() > 1 {
        let said: Vec<String> = declared
            .iter()
            .map(|(l, v)| format!("{l} says {v}"))
            .collect();
        findings.push(format!(
            "this repo declares {} different release versions ({}) — the release number is a claim with no single answer",
            numbers.len(),
            said.join(", ")
        ));
    } else if !numbers.is_empty() {
        let outside: Vec<&(String, String)> = declared
            .iter()
            .filter(|(l, _)| !l.ends_with("Cargo.toml"))
            .collect();
        if let Some((label, release)) = outside.first().map(|p| (&p.0, &p.1)) {
            for manifest in &manifests {
                let (Some(name), _) = toml.parse_package(&read(manifest)) else {
                    continue;
                };
                let Some(lv) = locked.get(&name).filter(|_| resolved.contains_key(&name)) else {
                    continue;
                };
                if stale.contains(&name) || lv == release {
                    continue;
                }
                findings.push(format!(
                    "{label}: the declared release version is {release} but {name} declares and locks {lv} — the release number was bumped in one place only"
                ));
            }
        } else {
            notes.push(
                "the only declared release version is the workspace's own; nothing outside the manifests declares a release number to compare the lockfile against"
                    .to_owned(),
            );
        }
    }
    Ok((findings, compared, notes))
}

/// (exit code, [(to stderr?, line)]).
fn run(root: Option<&str>, json: bool) -> (i32, Vec<(bool, String)>) {
    let raw = root.map_or_else(
        || crate::gitutil::repo_root().unwrap_or_else(|| ".".to_owned()),
        str::to_owned,
    );
    let root = Path::new(&raw)
        .canonicalize()
        .unwrap_or_else(|_| std::path::absolute(&raw).unwrap_or_else(|_| PathBuf::from(&raw)));
    if !root.is_dir() {
        return (
            2,
            vec![(
                true,
                format!("✗ [lock_version] not a directory: {}", root.display()),
            )],
        );
    }
    let spec = std::env::var("GOH_TAG_VERSION_SOURCES")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| crate::versrc::DEFAULT_SOURCES.join(" "));
    let (findings, examined, notes) = match audit(&root, &spec) {
        Ok(v) => v,
        Err(e) => return (2, vec![(true, format!("✗ [lock_version] {e}"))]),
    };
    if json {
        let doc = serde_json::json!({ "findings": findings, "examined": examined, "notes": notes });
        return (
            i32::from(!findings.is_empty()),
            vec![(false, crate::pyjson::dumps_indent2(&doc))],
        );
    }
    let mut lines: Vec<(bool, String)> = notes
        .iter()
        .map(|n| (false, format!("→ [lock_version] {n}")))
        .collect();
    lines.extend(
        findings
            .iter()
            .map(|f| (true, format!("✗ [lock_version] {f}"))),
    );
    if !findings.is_empty() {
        lines.push((
            true,
            format!(
                "✗ --- {} finding(s): Cargo.lock disagrees with the manifest ---",
                findings.len()
            ),
        ));
        return (1, lines);
    }
    if examined == 0 {
        lines.push((
            false,
            "→ [lock_version] no workspace crate compared — not applicable".to_owned(),
        ));
        return (0, lines);
    }
    lines.push((
        false,
        format!("✓ [lock_version] OK — {examined} workspace crate(s) agree between Cargo.toml and Cargo.lock"),
    ));
    (0, lines)
}

/// `goh lock-version [--root R] [--json]`.
#[must_use]
pub fn run_command(root: Option<&str>, json: bool) -> i32 {
    let (code, lines) = run(root, json);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// The structural step; the delegated step's label.
#[must_use]
pub fn step() -> Option<i32> {
    let label = "Cargo.lock matches its manifests";
    let start = crate::step_report::begin(label);
    match run(None, false) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/lockver.rs", "check_lock_version.py"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
