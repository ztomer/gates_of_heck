//! Cross-file markdown links -- Rust port of the retired `checks/check_md_links.py`
//! (Phase N1).
//!
//! A relative link must resolve to a file AND to an anchor that file has.
//! Both halves fail silently: a link to a missing heading renders and 404s
//! only on click, and the anchor is not the heading (`mdtext::slugify`).
//! Out of scope: links with a scheme, protocol-relative and site-absolute
//! ones; fenced blocks and inline code spans are not scanned.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};

use crate::mdtext::Fences;

/// `os.path.normpath` for a relative POSIX path.
fn normpath(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for part in path.split('/') {
        match part {
            "" | "." => {}
            ".." => {
                if parts.last().is_some_and(|p| *p != "..") {
                    parts.pop();
                } else {
                    parts.push("..");
                }
            }
            p => parts.push(p),
        }
    }
    if parts.is_empty() {
        ".".to_owned()
    } else {
        parts.join("/")
    }
}

fn dirname(rel: &str) -> &str {
    rel.rfind('/').map_or("", |i| &rel[..i])
}

struct Outcome {
    findings: Vec<String>,
    notes: Vec<String>,
    examined: usize,
    skipped: usize,
}

struct Tree<'a> {
    root: &'a Path,
    staged: bool,
    fences: &'a Fences,
    cache: HashMap<String, Option<BTreeSet<String>>>,
}

impl Tree<'_> {
    fn text(&self, rel: &str) -> Option<String> {
        let blob = crate::gitutil::content_bytes(self.root, rel, self.staged)?;
        if blob[..blob.len().min(8000)].contains(&0) {
            return None;
        }
        Some(String::from_utf8_lossy(&blob).into_owned())
    }

    fn anchors_of(&mut self, rel: &str) -> Option<BTreeSet<String>> {
        if !self.cache.contains_key(rel) {
            let got = self.text(rel).map(|t| self.fences.anchors(&t));
            self.cache.insert(rel.to_owned(), got);
        }
        self.cache.get(rel).cloned().flatten()
    }
}

fn check(
    root: &Path,
    exclude: Option<&str>,
    staged: bool,
    fences: &Fences,
) -> Result<Outcome, String> {
    let mut files: Vec<String> = crate::gitutil::listed_files(root, staged)?
        .into_iter()
        .filter(|f| f.to_lowercase().ends_with(".md"))
        .collect();
    if let Some(e) = exclude.filter(|e| !e.is_empty()) {
        let pattern = crate::pathfilter::PathFilter::new(e)
            .map_err(|x| format!("bad --exclude regex '{e}': {x}"))?;
        files.retain(|f| !pattern.is_match(f));
    }
    let mut out = Outcome {
        findings: Vec::new(),
        notes: Vec::new(),
        examined: files.len(),
        skipped: 0,
    };
    if files.is_empty() {
        out.notes
            .push("no tracked markdown files in scope — nothing to check".to_owned());
        return Ok(out);
    }
    let mut tree = Tree {
        root,
        staged,
        fences,
        cache: HashMap::new(),
    };
    for rel in &files {
        let Some(own) = tree.anchors_of(rel) else {
            out.notes
                .push(format!("{rel}: unreadable or binary — not checked"));
            continue;
        };
        let text = tree.text(rel).unwrap_or_default();
        for (number, target) in fences.links_in(&text) {
            if fences.skipped(&target) {
                out.skipped += 1;
                continue;
            }
            let (path, fragment) = target.split_once('#').unwrap_or((target.as_str(), ""));
            let (target_file, target_anchors) = if path.is_empty() {
                (rel.clone(), own.clone())
            } else {
                let joined = if dirname(rel).is_empty() {
                    path.to_owned()
                } else {
                    format!("{}/{path}", dirname(rel))
                };
                let resolved = normpath(&joined);
                if resolved.starts_with("..") {
                    out.findings.push(format!(
                        "{rel}:{number}: {target} points outside this repository — not resolvable from the tree"
                    ));
                    continue;
                }
                let mut exists = root.join(&resolved).exists();
                if !exists && !tree.cache.contains_key(&resolved) {
                    exists = tree.anchors_of(&resolved).is_some();
                }
                if !exists {
                    out.findings.push(format!(
                        "{rel}:{number}: {target} — no such file in this repo"
                    ));
                    continue;
                }
                if fragment.is_empty() {
                    continue;
                }
                let Some(anchors) = tree.anchors_of(&resolved) else {
                    continue;
                };
                (resolved, anchors)
            };
            if !fragment.is_empty() && !target_anchors.contains(fragment) {
                out.findings.push(format!(
                    "{rel}:{number}: {target} — {target_file} has no anchor #{fragment}"
                ));
            }
        }
    }
    Ok(out)
}

/// (exit code, [(to stderr?, line)]).
fn run(
    root: Option<&str>,
    staged: bool,
    exclude: Option<&str>,
    json: bool,
) -> (i32, Vec<(bool, String)>) {
    let raw = root.map_or_else(
        || crate::gitutil::repo_root().unwrap_or_else(|| ".".to_owned()),
        str::to_owned,
    );
    let root: PathBuf = Path::new(&raw)
        .canonicalize()
        .unwrap_or_else(|_| std::path::absolute(&raw).unwrap_or_else(|_| PathBuf::from(&raw)));
    if !root.is_dir() {
        return (
            2,
            vec![(
                true,
                format!("✗ [md_links] not a directory: {}", root.display()),
            )],
        );
    }
    let fences = match Fences::new() {
        Ok(f) => f,
        Err(e) => return (2, vec![(true, format!("✗ [md_links] {e}"))]),
    };
    let o = match check(&root, exclude, staged, &fences) {
        Ok(o) => o,
        Err(e) => return (2, vec![(true, format!("✗ [md_links] {e}"))]),
    };
    if json {
        let doc = serde_json::json!({
            "findings": o.findings, "notes": o.notes, "examined": o.examined, "skipped": o.skipped,
        });
        return (
            i32::from(!o.findings.is_empty()),
            vec![(false, crate::pyjson::dumps_indent2(&doc))],
        );
    }
    let mut lines: Vec<(bool, String)> = o
        .notes
        .iter()
        .map(|n| (false, format!("→ [md_links] {n}")))
        .collect();
    lines.extend(
        o.findings
            .iter()
            .map(|f| (true, format!("✗ [md_links] {f}"))),
    );
    if !o.findings.is_empty() {
        lines.push((
            true,
            format!(
                "✗ --- {} finding(s): a markdown link that resolves to nothing ---",
                o.findings.len()
            ),
        ));
        return (1, lines);
    }
    if o.examined == 0 {
        lines.push((
            false,
            "→ [md_links] no markdown examined — not applicable".to_owned(),
        ));
        return (0, lines);
    }
    let tail = if o.skipped > 0 {
        format!(", {} out-of-scope link(s) skipped", o.skipped)
    } else {
        String::new()
    };
    lines.push((
        false,
        format!(
            "✓ [md_links] OK — {} markdown file(s), every relative link resolves to a file and an anchor{tail}",
            o.examined
        ),
    ));
    (0, lines)
}

/// `goh md-links [--root R] [--staged] [--exclude RE] [--json]`.
/// With `anchors`, print the anchors and links of the markdown on stdin as
/// JSON instead (the parity harness's view of the grammar).
#[must_use]
pub fn run_command(
    root: Option<&str>,
    staged: bool,
    exclude: Option<&str>,
    json: bool,
    anchors: bool,
) -> i32 {
    if anchors {
        let mut text = String::new();
        let fences = match Fences::new() {
            Ok(f) if std::io::Read::read_to_string(&mut std::io::stdin(), &mut text).is_ok() => f,
            _ => {
                eprintln!("✗ [md_links] cannot read stdin or compile the grammar");
                return 2;
            }
        };
        let doc = serde_json::json!({
            "anchors": fences.anchors(&text).into_iter().collect::<Vec<_>>(),
            "links": fences.links_in(&text),
        });
        println!("{}", crate::pyjson::dumps(&doc));
        return 0;
    }
    let (code, lines) = run(root, staged, exclude, json);
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
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let label = if staged {
        "markdown links resolve (staged)"
    } else {
        "markdown links resolve"
    };
    let start = crate::step_report::begin(label);
    let exclude = (!cfg.exclude.is_empty()).then_some(cfg.exclude.as_str());
    match run(None, staged, exclude, false) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/mdlinks.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
