//! Workspace-lint opt-in audit — Rust port of `checks/check_lints_optin.py`.
//!
//! `[workspace.lints]` is a declaration, not an application: a member
//! crate inherits it only with `[lints] workspace = true` of its own,
//! and cargo never warns about the omission. This walks every workspace
//! under the repo root and fails crates that state no inheritance.
//!
//! The reference parses manifests with regexes, not TOML — mirrored
//! here with `(?m)`/`(?s)` flags inline, since the `regex` crate has no
//! flag arguments. `--staged` is accepted and ignored, exactly like the
//! reference (the audit is whole-workspace by nature).

use std::fmt::Write as _;

/// Workspace policy declaration. Mirrors `WORKSPACE_LINTS`.
const WORKSPACE_LINTS: &str = r"(?m)^\s*\[workspace\.lints(\.\w+)?\]";
/// Any crate-level lints table. Mirrors `LINTS_TABLE`.
const LINTS_TABLE: &str = r"(?m)^\s*\[lints(\.\w+)?\]";
/// Crate-level inheritance of the workspace policy. Mirrors
/// `LINTS_WORKSPACE`.
const LINTS_WORKSPACE: &str = r"(?ms)^\s*\[lints\]\s*$.*?^\s*workspace\s*=\s*true";
/// Workspace member list. Mirrors `MEMBERS`.
const MEMBERS: &str = r"(?ms)^\s*members\s*=\s*\[(.*?)\]";
/// Workspace exclusion list. Mirrors `EXCLUDE`.
const EXCLUDE: &str = r"(?ms)^\s*exclude\s*=\s*\[(.*?)\]";
/// Quoted list entries. Mirrors the `_entries` finder.
const QUOTED_ENTRY: &str = r#""([^"]+)""#;

/// Compiled manifest patterns.
pub struct Scanner {
    /// Workspace policy declarations.
    workspace_lints: regex::Regex,
    /// Crate-level lint tables.
    lints_table: regex::Regex,
    /// Crate-level workspace inheritance.
    lints_workspace: regex::Regex,
    /// Member lists.
    members: regex::Regex,
    /// Exclusion lists.
    exclude: regex::Regex,
    /// Quoted entries.
    quoted_entry: regex::Regex,
}

impl Scanner {
    /// Compile every built-in pattern.
    ///
    /// # Errors
    ///
    /// Returns a message only if a built-in pattern fails to compile,
    /// which is a programming error, never input-dependent.
    pub fn compile() -> Result<Self, String> {
        fn one(pattern: &str) -> Result<regex::Regex, String> {
            regex::Regex::new(pattern).map_err(|e| format!("built-in lints pattern failed: {e}"))
        }
        Ok(Self {
            workspace_lints: one(WORKSPACE_LINTS)?,
            lints_table: one(LINTS_TABLE)?,
            lints_workspace: one(LINTS_WORKSPACE)?,
            members: one(MEMBERS)?,
            exclude: one(EXCLUDE)?,
            quoted_entry: one(QUOTED_ENTRY)?,
        })
    }

    /// Quoted entries of a `members`/`exclude` list, mirroring `_entries`
    /// (absent list → no entries).
    fn entries(&self, text: &str, list: &regex::Regex) -> Vec<String> {
        list.captures(text).map_or_else(Vec::new, |caps| {
            self.quoted_entry
                .captures_iter(&caps[1])
                .map(|c| c[1].to_owned())
                .collect()
        })
    }
}

/// One crate finding.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// Manifest path, repo-root-relative.
    pub manifest: String,
    /// Which of the two report texts applies.
    pub own_table: bool,
}

/// Audit every workspace under `root`.
///
/// Returns findings plus the inspected-member count. Mirrors `audit()`:
/// manifests sorted, `target` and `references` trees skipped, members
/// without a manifest skipped.
///
/// Unreadable member manifests are skipped: the reference would raise an
/// uncaught traceback there, which is a crash, not a verdict — no parity
/// surface exists for corrupt trees.
#[must_use]
pub fn audit(scanner: &Scanner, root: &std::path::Path) -> (Vec<Finding>, usize) {
    let mut manifests = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path.file_name().is_some_and(|n| n == "Cargo.toml") {
                manifests.push(path);
            }
        }
    }
    manifests.sort();
    let mut findings = Vec::new();
    let mut inspected = 0;
    for manifest in manifests {
        if manifest
            .components()
            .any(|c| c.as_os_str() == "target" || c.as_os_str() == "references")
        {
            continue;
        }
        let text = std::fs::read_to_string(&manifest).unwrap_or_default();
        if !scanner.workspace_lints.is_match(&text) {
            continue;
        }
        let base = manifest.parent().unwrap_or(root);
        let excluded: std::collections::BTreeSet<String> = scanner
            .entries(&text, &scanner.exclude)
            .into_iter()
            .collect();
        for member in scanner.entries(&text, &scanner.members) {
            if excluded.contains(&member) {
                continue;
            }
            let member_manifest = base.join(&member).join("Cargo.toml");
            if !member_manifest.is_file() {
                continue;
            }
            inspected += 1;
            let body = std::fs::read_to_string(&member_manifest).unwrap_or_default();
            if scanner.lints_workspace.is_match(&body) {
                continue;
            }
            let rel = member_manifest.strip_prefix(root).map_or_else(
                |_| member_manifest.to_string_lossy().into_owned(),
                |r| r.to_string_lossy().into_owned(),
            );
            findings.push(Finding {
                manifest: rel,
                own_table: scanner.lints_table.is_match(&body),
            });
        }
    }
    (findings, inspected)
}

/// Full violation block. Streams mirror the reference (`err` → stderr
/// with ✗, `info` → stdout with →): `(ok, stdout_text, stderr_text)`.
#[must_use]
pub fn format_report(findings: &[Finding], inspected: usize) -> (bool, String, String) {
    if inspected == 0 {
        return (
            true,
            "✓ [lints_optin] no workspace declares [workspace.lints] — nothing to inherit\n"
                .to_owned(),
            String::new(),
        );
    }
    if findings.is_empty() {
        return (
            true,
            format!(
                "✓ [lints_optin] OK — {inspected} workspace member(s) inherit the declared policy\n"
            ),
            String::new(),
        );
    }
    let mut err = format!(
        "✗ [lints_optin] {} crate(s) not subject to the workspace lint policy:\n",
        findings.len()
    );
    for finding in findings {
        // Every line goes through `err()` separately in the reference,
        // so each carries the ✗ prefix — including the findings.
        if finding.own_table {
            let _ = writeln!(
                err,
                "✗     {}: has its own [lints] table but does not inherit the workspace policy ([lints] workspace = true)",
                finding.manifest
            );
        } else {
            let _ = writeln!(
                err,
                "✗     {}: no [lints] table — this crate inherits NONE of the workspace's declared lints",
                finding.manifest
            );
        }
    }
    let out = "→   `[workspace.lints]` is a declaration; a crate applies it with:\n→       [lints]\n→       workspace = true\n→   Without that line the crate has no findings because it is subject\n→   to no lints — which reads exactly like a clean crate.\n"
        .to_owned();
    (false, out, err)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scanner() -> Scanner {
        Scanner::compile().expect("built-in patterns compile")
    }

    fn write(root: &std::path::Path, rel: &str, body: &str) {
        let dest = root.join(rel);
        std::fs::create_dir_all(dest.parent().expect("parent")).expect("mkdir");
        std::fs::write(dest, body).expect("write");
    }

    #[test]
    fn missing_inheritance_fails_own_table_fails_differently() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        write(
            root,
            "Cargo.toml",
            "[workspace]\nmembers = [\"a\", \"b\"]\n\n[workspace.lints.clippy]\npedantic = \"warn\"\n",
        );
        write(
            root,
            "a/Cargo.toml",
            "[package]\nname = \"a\"\n\n[lints]\nworkspace = true\n",
        );
        write(root, "b/Cargo.toml", "[package]\nname = \"b\"\n");
        let (findings, inspected) = audit(&scanner(), root);
        assert_eq!(inspected, 2);
        assert_eq!(findings.len(), 1);
        assert_eq!(findings[0].manifest, "b/Cargo.toml");
        assert!(!findings[0].own_table);
    }

    #[test]
    fn excluded_members_and_target_trees_are_out_of_scope() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        write(
            root,
            "Cargo.toml",
            "[workspace]\nmembers = [\"a\", \"vendored\"]\nexclude = [\"vendored\"]\n\n[workspace.lints.clippy]\nall = \"warn\"\n",
        );
        write(
            root,
            "a/Cargo.toml",
            "[package]\nname = \"a\"\n\n[lints]\nworkspace = true\n",
        );
        write(
            root,
            "target/debug/Cargo.toml",
            "[workspace.lints.clippy]\nall = \"warn\"\n",
        );
        let (findings, inspected) = audit(&scanner(), root);
        assert_eq!((findings.len(), inspected), (0, 1));
    }

    #[test]
    fn no_policy_workspace_reports_nothing_to_inherit() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        write(root, "Cargo.toml", "[workspace]\nmembers = [\"a\"]\n");
        write(root, "a/Cargo.toml", "[package]\nname = \"a\"\n");
        let (findings, inspected) = audit(&scanner(), root);
        assert_eq!((findings.len(), inspected), (0, 0));
        let (ok, out, err) = format_report(&findings, inspected);
        assert!(ok && err.is_empty() && out.contains("nothing to inherit"));
    }
}
