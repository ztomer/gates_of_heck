//! A committed `Cargo.lock` must agree with its manifests -- Rust port of
//! the retired `checks/check_lock_version.py` and `checks/_cargo_toml.py` (Phase N1).
//!
//! `app_updates`, 2026-10-01: the release bumped `[workspace.package] version`
//! and shipped a lockfile one version behind; any `cargo build` heals the
//! tree, so the defect lives only in the COMMIT. Arm 1 compares every
//! workspace member's version (inheritance resolved) with its lock entry;
//! arm 2 compares the release version a source OUTSIDE the manifests declares
//! (`GOH_TAG_VERSION_SOURCES`) with what the members declare and lock.

use goh_tui::{ERR, OK, START};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

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

/// The manifests and the lockfile, read by a TOML parser (BACKLOG 6.18).
///
/// A line regex read `version = "1.9.0"  # a comment` as no version at all, so Finance/zinc's
/// `zinc-payroll` read as INHERITING the workspace's 4.8.0 and its correct lock entry as stale.
/// A manifest that is not TOML is a finding naming the file: cargo could not build it either.
struct Toml;

/// A manifest's `[package]`: its name, and its version line.
struct Package {
    name: Option<String>,
    version: Version,
}

/// What a `[package] version` says.
#[derive(Clone, PartialEq, Eq)]
enum Version {
    /// `version = "x.y.z"`: this crate's own version line.
    Own(String),
    /// `version.workspace = true`, or no version at all: the workspace's, when it declares one.
    Inherited,
}

fn parse(text: &str, label: &str) -> Result<toml::Table, String> {
    text.parse::<toml::Table>()
        .map_err(|e| format!("{label} is not valid TOML: {}", e.message()))
}

fn string(table: Option<&toml::Value>, key: &str) -> Option<String> {
    table
        .and_then(|t| t.get(key))
        .and_then(toml::Value::as_str)
        .map(str::to_owned)
}

impl Toml {
    /// `{name: version}` for every `[[package]]` (first entry of a name wins).
    fn parse_lock(text: &str) -> Result<BTreeMap<String, String>, String> {
        let lock = parse(text, "Cargo.lock")?;
        let mut out = BTreeMap::new();
        for pkg in lock
            .get("package")
            .and_then(toml::Value::as_array)
            .into_iter()
            .flatten()
        {
            if let (Some(n), Some(v)) = (string(Some(pkg), "name"), string(Some(pkg), "version")) {
                out.entry(n).or_insert(v);
            }
        }
        Ok(out)
    }

    fn parse_package(text: &str, label: &str) -> Result<Package, String> {
        let manifest = parse(text, label)?;
        let package = manifest.get("package");
        let version = match package.and_then(|p| p.get("version")) {
            Some(toml::Value::String(v)) => Version::Own(v.clone()),
            _ => Version::Inherited,
        };
        Ok(Package {
            name: string(package, "name"),
            version,
        })
    }

    /// `([workspace.package] version, members)`.
    fn parse_workspace(text: &str) -> Result<(Option<String>, Vec<String>), String> {
        let manifest = parse(text, "Cargo.toml")?;
        let ws = manifest.get("workspace");
        let members = ws
            .and_then(|w| w.get("members"))
            .and_then(toml::Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(toml::Value::as_str)
            .map(str::to_owned)
            .collect();
        Ok((
            string(ws.and_then(|w| w.get("package")), "version"),
            members,
        ))
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
    let mut notes = Vec::new();
    let lock = root.join("Cargo.lock");
    if !lock.is_file() {
        notes.push("no Cargo.lock in this repository — nothing to compare".to_owned());
        return Ok((Vec::new(), 0, notes));
    }
    let root_manifest = root.join("Cargo.toml");
    if !root_manifest.is_file() {
        notes.push("no Cargo.toml beside Cargo.lock — nothing to compare".to_owned());
        return Ok((Vec::new(), 0, notes));
    }
    let unreadable = |e: String| Ok((vec![e], 0, Vec::new()));
    let locked = match Toml::parse_lock(&read(&lock)) {
        Ok(l) => l,
        Err(e) => return unreadable(e),
    };
    let root_text = read(&root_manifest);
    let ((workspace_version, members), root_pkg) = match Toml::parse_workspace(&root_text)
        .and_then(|w| Toml::parse_package(&root_text, "Cargo.toml").map(|p| (w, p)))
    {
        Ok(v) => v,
        Err(e) => return unreadable(e),
    };
    let mut manifests = member_manifests(root, &members);
    let root_versioned = matches!(root_pkg.version, Version::Own(_)) || workspace_version.is_some();
    if root_pkg.name.is_some() && root_versioned {
        manifests.insert(0, root_manifest);
    }
    if manifests.is_empty() {
        notes.push(
            "this manifest declares no package and no workspace members — nothing to compare"
                .to_owned(),
        );
        return Ok((Vec::new(), 0, notes));
    }
    let mut findings = Vec::new();
    // The members whose version IS the release: those inheriting it, or, where the workspace
    // declares no version to inherit, every member (media_server's per-crate literals). A member
    // writing its own literal under a workspace version has opted out (6.18: zinc-payroll).
    let mut release_members: Vec<(String, String)> = Vec::new();
    let mut stale: BTreeSet<String> = BTreeSet::new();
    let mut compared = 0usize;
    for manifest in &manifests {
        let r = rel(root, manifest);
        let pkg = match Toml::parse_package(&read(manifest), &r) {
            Ok(p) => p,
            Err(e) => {
                findings.push(e);
                continue;
            }
        };
        let Some(name) = pkg.name.filter(|n| !n.is_empty()) else {
            notes.push(format!("{r}: no [package] name — not compared"));
            continue;
        };
        let (version, tracks_release) = match (&pkg.version, &workspace_version) {
            (Version::Own(v), None) => (v.clone(), true),
            (Version::Own(v), Some(_)) => (v.clone(), false),
            (Version::Inherited, Some(w)) => (w.clone(), true),
            (Version::Inherited, None) => {
                notes.push(format!(
                    "{r}: inherits a version and no [workspace.package] declares one — not compared"
                ));
                continue;
            }
        };
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
            Some(lv) => {
                if tracks_release {
                    release_members.push((name, lv.clone()));
                }
            }
        }
    }
    release_findings(
        root,
        spec,
        &release_members,
        &stale,
        &mut findings,
        &mut notes,
    )?;
    Ok((findings, compared, notes))
}

/// Arm 2: the release number a source OUTSIDE the manifests declares, against what each member
/// that carries the release declares and locks.
fn release_findings(
    root: &Path,
    spec: &str,
    members: &[(String, String)],
    stale: &BTreeSet<String>,
    findings: &mut Vec<String>,
    notes: &mut Vec<String>,
) -> Result<(), String> {
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
        return Ok(());
    }
    if numbers.is_empty() {
        return Ok(());
    }
    let outside = declared.iter().find(|(l, _)| !l.ends_with("Cargo.toml"));
    let Some((label, release)) = outside else {
        notes.push(
            "the only declared release version is the workspace's own; nothing outside the manifests declares a release number to compare the lockfile against"
                .to_owned(),
        );
        return Ok(());
    };
    for (name, lv) in members {
        if stale.contains(name) || lv == release {
            continue;
        }
        findings.push(format!(
            "{label}: the declared release version is {release} but {name} declares and locks {lv} — the release number was bumped in one place only"
        ));
    }
    Ok(())
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
                format!("{ERR} [lock_version] not a directory: {}", root.display()),
            )],
        );
    }
    let spec = std::env::var("GOH_TAG_VERSION_SOURCES")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| crate::versrc::DEFAULT_SOURCES.join(" "));
    let (findings, examined, notes) = match audit(&root, &spec) {
        Ok(v) => v,
        Err(e) => return (2, vec![(true, format!("{ERR} [lock_version] {e}"))]),
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
        .map(|n| (false, format!("{START} [lock_version] {n}")))
        .collect();
    lines.extend(
        findings
            .iter()
            .map(|f| (true, format!("{ERR} [lock_version] {f}"))),
    );
    if !findings.is_empty() {
        lines.push((
            true,
            format!(
                "{ERR} --- {} finding(s): Cargo.lock disagrees with the manifest ---",
                findings.len()
            ),
        ));
        return (1, lines);
    }
    if examined == 0 {
        lines.push((
            false,
            format!("{START} [lock_version] no workspace crate compared — not applicable"),
        ));
        return (0, lines);
    }
    lines.push((
        false,
        format!("{OK} [lock_version] OK — {examined} workspace crate(s) agree between Cargo.toml and Cargo.lock"),
    ));
    (0, lines)
}

/// `goh lock-version [--root R] [--json]`.
#[must_use]
pub fn run_command(root: Option<&str>, json: bool) -> i32 {
    let (code, lines) = run(root, json);
    for (to_err, line) in lines {
        if to_err {
            goh_tui::eoutln(&line);
        } else {
            goh_tui::outln(&line);
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
                &crate::step_report::ported("crates/goh/src/lockver.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
