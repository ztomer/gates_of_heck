//! Version provenance -- Rust port of `checks/check_version_provenance.py`
//! and `checks/_rust_crates.py` (Phase N1).
//!
//! A shipped binary should say WHICH build it is, not only which number: a
//! commit answers "what am I actually running?". STATIC by design -- a gate
//! that must run every repo's binary is skipped on exactly the repos it would
//! catch -- so it asserts the declaration: a clap `version` in `src/main.rs`,
//! `src/lib.rs` or `src/bin/*.rs` of a package that builds a binary must embed
//! a commit and a build date, set by a build script; an opaque `*PROVENANCE*`
//! clause must be derived from a commit, a clock and the working tree's state.
//! `.gates-version-baseline.json` may only shrink, and an entry that
//! suppresses nothing is a finding.

use std::collections::BTreeSet;
use std::fmt::Write as _;
use std::path::{Path, PathBuf};

use regex::Regex;

const MAIN_FILES: [&str; 2] = ["src/main.rs", "src/lib.rs"];
const SKIP: [&str; 4] = ["target", ".build", "vendor", "node_modules"];

/// The compiled rules.
pub struct Rules {
    hash_env: Regex,
    date_env: Regex,
    clause_env: Regex,
    command_attr: Regex,
    bare_version: Regex,
    long_version: Regex,
    quoted: Regex,
    rustc_env: Regex,
    commit_src: Regex,
    date_src: Regex,
    tree_src: Regex,
}

fn rx(p: &str) -> Result<Regex, String> {
    Regex::new(p).map_err(|e| format!("version provenance pattern {p:?}: {e}"))
}

impl Rules {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            hash_env: rx(
                r#"(?i)env!\(\s*"[A-Z0-9_]*(GIT_HASH|GIT_SHA|COMMIT_SHA|COMMIT|BUILD_HASH|VCS_HASH|SOURCE_HASH)[A-Z0-9_]*"\s*\)"#,
            )?,
            date_env: rx(
                r#"(?i)env!\(\s*"[A-Z0-9_]*(BUILD_DATE|BUILD_TIME|BUILD_TIMESTAMP|BUILT_AT|SOURCE_DATE)[A-Z0-9_]*"\s*\)"#,
            )?,
            clause_env: rx(r#"(?i)env!\(\s*"([A-Z0-9_]*PROVENANCE[A-Z0-9_]*)"\s*\)"#)?,
            command_attr: rx(r"(?s)#\s*\[\s*command\s*\((.*?)\)\s*\]")?,
            bare_version: rx(r"(?:^|,)\s*version\s*(?:,|$)")?,
            long_version: rx(r#"\blong_version\s*=|\bversion\s*=\s*""#)?,
            quoted: rx(r#""([^"]+)""#)?,
            rustc_env: rx(r"rustc-env=([A-Z0-9_]+)")?,
            commit_src: rx(
                r"(?i)rev-parse|rev_list|rev-list|write-tree|write_tree|describe|diff-tree|diff_tree",
            )?,
            date_src: rx(
                r"(?i)SOURCE_DATE_EPOCH|SystemTime|UNIX_EPOCH|civil_from_days|build_date|%Y-%m-%d|%Y%m%d",
            )?,
            tree_src: rx(
                r"(?i)write-tree|write_tree|--porcelain|diff-index|diff_index|is_dirty|dirty",
            )?,
        })
    }

    fn declares_version(&self, text: &str) -> bool {
        self.command_attr.captures_iter(text).any(|c| {
            c.get(1).is_some_and(|b| {
                self.bare_version.is_match(b.as_str()) || self.long_version.is_match(b.as_str())
            })
        })
    }

    fn sets_named_env(&self, pkg: &Path, name: &str) -> bool {
        build_scripts(pkg).iter().any(|text| {
            self.rustc_env
                .captures_iter(text)
                .any(|c| c.get(1).is_some_and(|m| m.as_str() == name))
        })
    }

    fn clause_is_backed(&self, pkg: &Path, name: &str) -> Vec<String> {
        let scripts: Vec<String> = build_scripts(pkg)
            .into_iter()
            .filter(|text| {
                self.rustc_env
                    .captures_iter(text)
                    .any(|c| c.get(1).is_some_and(|m| m.as_str() == name))
            })
            .collect();
        if scripts.is_empty() {
            return vec![format!(
                "the version string embeds {name} but no build script sets it \
                 (expected a build.rs emitting cargo:rustc-env={name}=)"
            )];
        }
        let text = scripts.join("\n");
        let missing: Vec<&str> = [
            ("a commit", &self.commit_src),
            ("a build date", &self.date_src),
            ("the working tree's state", &self.tree_src),
        ]
        .into_iter()
        .filter(|(_, p)| !p.is_match(&text))
        .map(|(label, _)| label)
        .collect();
        if missing.is_empty() {
            return Vec::new();
        }
        vec![format!(
            "the version string embeds {name} but the build script setting it \
             never derives {} -- a provenance field that \
             cannot tell a dirty build from a clean one is not provenance",
            missing.join(" and ")
        )]
    }

    /// Findings for one version-declaring file, in package `pkg`.
    fn problems(&self, pkg: &Path, text: &str) -> Vec<String> {
        let clause = self
            .clause_env
            .captures(text)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_owned());
        let has_hash = self.hash_env.is_match(text) || clause.is_some();
        let has_date = self.date_env.is_match(text) || clause.is_some();
        let mut out = Vec::new();
        if !has_hash {
            out.push(
                "declares a version but never embeds a commit hash \
                 (expected an env!(\"...GIT_HASH\")-style field)"
                    .to_owned(),
            );
        }
        if !has_date {
            out.push(
                "declares a version but never embeds a build date \
                 (expected an env!(\"...BUILD_DATE\")-style field)"
                    .to_owned(),
            );
        }
        if !(has_hash && has_date) {
            return out;
        }
        let Some(clause) = clause else {
            for (pattern, kind) in [
                (&self.hash_env, "commit hash"),
                (&self.date_env, "build date"),
            ] {
                let name = pattern
                    .find(text)
                    .and_then(|m| self.quoted.captures(m.as_str()))
                    .and_then(|c| c.get(1))
                    .map_or("", |m| m.as_str());
                if !self.sets_named_env(pkg, name) {
                    out.push(format!(
                        "the version string references {kind} but no build script \
                         sets it (expected a build.rs emitting cargo:rustc-env={name}=)"
                    ));
                }
            }
            return out;
        };
        self.clause_is_backed(pkg, &clause)
    }
}

fn read_lossy(path: &Path) -> Option<String> {
    std::fs::read(path)
        .ok()
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

fn build_scripts(pkg: &Path) -> Vec<String> {
    ["build.rs", "build/build.rs"]
        .iter()
        .map(|n| pkg.join(n))
        .filter(|p| p.is_file())
        .filter_map(|p| read_lossy(&p))
        .collect()
}

/// Every package directory (workspace members included), sorted; the root
/// itself when the tree has no manifest.
fn package_roots(root: &Path) -> Vec<PathBuf> {
    let mut seen: BTreeSet<PathBuf> = BTreeSet::new();
    for rel in crate::gitutil::tree_files(root) {
        if rel.rsplit('/').next() != Some("Cargo.toml")
            || rel.split('/').any(|part| SKIP.contains(&part))
        {
            continue;
        }
        let manifest = root.join(&rel);
        if manifest.is_file() {
            if let Some(parent) = manifest.parent() {
                seen.insert(parent.to_path_buf());
            }
        }
    }
    if seen.is_empty() {
        seen.insert(root.to_path_buf());
    }
    seen.into_iter().collect()
}

fn rs_files_in(dir: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut out: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.as_bytes().ends_with(b".rs"))
        })
        .collect();
    out.sort();
    out
}

/// Every file that could carry a binary's version string.
fn binary_roots(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for pkg in roots {
        let manifest = pkg.join("Cargo.toml");
        if manifest.is_file() {
            let text = read_lossy(&manifest).unwrap_or_default();
            let declares_bin = text.contains("[[bin]]")
                || pkg.join("src/main.rs").is_file()
                || !rs_files_in(&pkg.join("src/bin")).is_empty();
            if !declares_bin {
                continue;
            }
        }
        out.extend(
            MAIN_FILES
                .iter()
                .map(|m| pkg.join(m))
                .filter(|p| p.is_file()),
        );
        out.extend(rs_files_in(&pkg.join("src/bin")));
    }
    out.retain(|p| p.is_file());
    out
}

fn rel_of(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

/// `(problems, examined)` over the tree at `root`.
///
/// # Errors
/// The index cannot be listed at `--staged`: an unlistable index is not an
/// empty commit.
pub fn check(
    rules: &Rules,
    root: &Path,
    allow: &BTreeSet<String>,
    staged: bool,
) -> Result<(Vec<String>, usize), String> {
    let roots = package_roots(root);
    let scope: Option<BTreeSet<String>> = if staged {
        Some(
            crate::gitutil::listed_files(root, true)?
                .into_iter()
                .filter(|f| f.as_bytes().ends_with(b".rs"))
                .collect(),
        )
    } else {
        None
    };
    let mut problems = Vec::new();
    let mut declaring: BTreeSet<String> = BTreeSet::new();
    for path in binary_roots(&roots) {
        let rel = rel_of(root, &path);
        if scope.as_ref().is_some_and(|s| !s.contains(&rel)) {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, &rel, staged) else {
            continue;
        };
        let text = String::from_utf8_lossy(&blob);
        if !rules.declares_version(&text) {
            continue;
        }
        declaring.insert(rel.clone());
        if allow.contains(&rel) || allow.contains(&path.to_string_lossy().into_owned()) {
            continue;
        }
        let mut pkg = root.to_path_buf();
        let mut depth = None;
        for cand in roots.iter().filter(|c| path.starts_with(c)) {
            let parts = cand.components().count();
            if depth.is_none_or(|d| parts > d) {
                depth = Some(parts);
                pkg.clone_from(cand);
            }
        }
        for p in rules.problems(&pkg, &text) {
            problems.push(format!("{rel}: {p}"));
        }
    }
    let everywhere = || -> BTreeSet<String> {
        binary_roots(&roots)
            .into_iter()
            .filter(|p| read_lossy(p).is_some_and(|t| rules.declares_version(&t)))
            .map(|p| rel_of(root, &p))
            .collect()
    };
    for entry in allow {
        if declaring.contains(entry) || (staged && everywhere().contains(entry)) {
            continue;
        }
        problems.push(format!(
            ".gates-version-baseline.json: entry {entry} names no \
             version-declaring source file -- it suppresses nothing; delete the entry"
        ));
    }
    Ok((problems, declaring.len()))
}

/// Read a baseline: a JSON array of paths (an object's keys, as `set()` reads them).
///
/// # Errors
/// An unreadable file or JSON that is not an array or object.
pub fn read_baseline(path: &Path) -> Result<BTreeSet<String>, String> {
    let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let value: serde_json::Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
    match value {
        serde_json::Value::Array(items) => Ok(items
            .into_iter()
            .map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_owned))
            .collect()),
        serde_json::Value::Object(map) => Ok(map.into_iter().map(|(k, _)| k).collect()),
        _ => Err("not a JSON array of paths".to_owned()),
    }
}

/// The reference's text report: (exit code, stdout).
#[must_use]
pub fn report(problems: &[String], examined: usize, json: bool) -> (i32, String) {
    let code = i32::from(!problems.is_empty());
    if json {
        let doc = serde_json::json!({ "findings": problems, "examined": examined });
        return (code, format!("{}\n", crate::pyjson::dumps_indent2(&doc)));
    }
    let mut s = String::new();
    for p in problems {
        let _ = writeln!(s, "✗ [version_provenance] {p}");
    }
    if !problems.is_empty() {
        let _ = writeln!(s, "--- {} finding(s) ---", problems.len());
        return (1, s);
    }
    if examined == 0 {
        s.push_str(
            "⚠ [version_provenance] no version-declaring source files in scope \
             — nothing examined (not applicable)\n",
        );
        return (0, s);
    }
    let _ = writeln!(
        s,
        "✓ [version_provenance] all {examined} version-declaring source file(s) \
         carry a commit and a build date"
    );
    (0, s)
}

/// `goh version-provenance [ROOT] [--baseline F] [--staged] [--json]`.
#[must_use]
pub fn run_command(root: &str, baseline: Option<&str>, staged: bool, json: bool) -> i32 {
    let (code, out, err) = run(Path::new(root), baseline, staged, json);
    print!("{out}");
    eprint!("{err}");
    code
}

/// (exit code, stdout, stderr), the reference's split.
#[must_use]
pub fn run(root: &Path, baseline: Option<&str>, staged: bool, json: bool) -> (i32, String, String) {
    let Some(root) = root.canonicalize().ok().filter(|r| r.is_dir()) else {
        let shown = std::path::absolute(root).unwrap_or_else(|_| root.to_path_buf());
        return (
            2,
            String::new(),
            format!("✗ not a directory: {}\n", shown.display()),
        );
    };
    let mut allow = BTreeSet::new();
    if let Some(b) = baseline.filter(|b| Path::new(b).is_file()) {
        match read_baseline(Path::new(b)) {
            Ok(set) => allow = set,
            Err(e) => {
                return (
                    2,
                    String::new(),
                    format!("✗ cannot read baseline {b}: {e}\n"),
                )
            }
        }
    }
    let rules = match Rules::new() {
        Ok(r) => r,
        Err(e) => return (2, String::new(), format!("✗ {e}\n")),
    };
    let (problems, examined) = match check(&rules, &root, &allow, staged) {
        Ok(found) => found,
        Err(e) => return (2, String::new(), format!("✗ [version_provenance] {e}\n")),
    };
    let (code, out) = report(&problems, examined, json);
    (code, out, String::new())
}

/// The structural step: runs when the repo ships a baseline, as the delegated
/// step did; the label is the delegated step's.
#[must_use]
pub fn step(repo: &Path, staged: bool) -> Option<i32> {
    let baseline = ".gates-version-baseline.json";
    if !repo.join(baseline).is_file() {
        return None;
    }
    let label = "version provenance";
    let start = crate::step_report::begin(label);
    let path = repo.join(baseline);
    match run(repo, path.to_str(), staged, false) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, out, err) => {
            let _ = crate::step_report::fail(label, "native", &format!("{out}{err}"), start);
            Some(code)
        }
    }
}
