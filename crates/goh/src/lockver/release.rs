//! Arm 2 of `goh lock-version`: the release number a source outside the manifests declares,
//! against what the members that carry the release declare and lock.

use super::{glob, read, rel};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

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
                vec![(path, p)]
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

/// `(name, locked version, manifest path when the member writes its own version literal)`.
pub type Member = (String, String, Option<String>);

/// The version a manifest declares when its release number lives elsewhere (goh's is its tag,
/// GOH.SHIPRELREUSE): cargo's own "unpublished" version, never a release anyone cut.
const PLACEHOLDER: &str = "0.0.0";

/// Arm 2: the release number a source OUTSIDE the manifests declares, against what each member
/// that carries the release declares and locks.
pub fn findings(
    root: &Path,
    spec: &str,
    members: &[Member],
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
    // A `cargo:` source names the manifests that carry the release: an own-literal member none of
    // them reads (a helper crate on its own 0.1.0) is no carrier, whatever the release says.
    let named: BTreeSet<&str> = declared
        .iter()
        .map(|(l, _)| l.as_str())
        .filter(|l| l.ends_with("Cargo.toml"))
        .collect();
    for (name, lv, own) in members {
        if stale.contains(name) || lv == release {
            continue;
        }
        if own
            .as_deref()
            .is_some_and(|m| !named.is_empty() && !named.contains(m))
        {
            continue;
        }
        if lv == PLACEHOLDER {
            notes.push(format!(
                "{name} declares the placeholder {PLACEHOLDER}: its release number is not in the manifests (GOH.SHIPRELREUSE), so {label}'s {release} is nothing to compare"
            ));
            continue;
        }
        findings.push(format!(
            "{label}: the declared release version is {release} but {name} declares and locks {lv} — the release number was bumped in one place only"
        ));
    }
    Ok(())
}
