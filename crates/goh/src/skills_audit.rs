//! Skills-corpus audit stages.
//!
//! One skill, links, duplicates, re-record. Called by `skills::audit`;
//! the tables, baseline loader and orchestrator stay there. Each stage
//! is small enough to hold the whole reference branch it mirrors in
//! view at once.

use std::collections::{BTreeMap, BTreeSet};

use crate::skills::{
    frontmatter, AuditInputs, AuditOutcome, BaselineEntry, Scanner, LESSON_TITLE_CHARS,
    TITLE_DISPLAY_CHARS,
};

/// One skill's findings: violation lines, extra reference files read,
/// word count, and section titles for the duplicates stage.
pub(crate) struct SkillFindings {
    /// Violation lines (without the `✗` prefix).
    pub(crate) lines: Vec<String>,
    /// Reference files read beyond SKILL.md.
    pub(crate) extra_files: usize,
    /// Word count.
    pub(crate) words: u64,
    /// Section titles (lowercased) for the duplicates stage.
    pub(crate) sections: Vec<String>,
}

/// Shared audit context: everything every skill needs except its own
/// name and text. One argument instead of seven.
pub(crate) struct SkillCtx<'a> {
    /// Compiled patterns.
    pub(crate) scanner: &'a Scanner,
    /// Corpus root.
    pub(crate) root: &'a std::path::Path,
    /// All skill names (wikilink targets).
    pub(crate) names: &'a BTreeSet<&'a str>,
    /// Seeded oversized entries.
    pub(crate) baseline: &'a BTreeMap<String, BaselineEntry>,
    /// Word ceiling as a number.
    pub(crate) ceiling: u64,
    /// Word ceiling for messages.
    pub(crate) max_words: usize,
}

/// Frontmatter, links, and size for one skill. The size branch mirrors
/// the reference exactly: over the ceiling with no baselined number is a
/// fresh violation; over the baselined number is ratchet growth; under
/// the ceiling with a live entry is stale.
pub(crate) fn audit_skill(ctx: &SkillCtx<'_>, name: &str, text: &str) -> SkillFindings {
    let mut skill = SkillFindings {
        lines: Vec::new(),
        extra_files: 0,
        words: u64::try_from(text.split_whitespace().count()).unwrap_or(u64::MAX),
        sections: Vec::new(),
    };
    let rel = format!("{name}/SKILL.md");
    match frontmatter(text) {
        None => skill.lines.push(format!(
            "{rel}: no YAML frontmatter — this skill can never be triggered"
        )),
        Some(fm) => {
            match ctx.scanner.fm_name.captures(fm) {
                None => skill
                    .lines
                    .push(format!("{rel}: frontmatter has no `name:`")),
                Some(caps) => {
                    if &caps[1] != name {
                        skill.lines.push(format!(
                            "{rel}: name `{}` does not match directory `{name}`",
                            &caps[1]
                        ));
                    }
                }
            }
            if !ctx.scanner.fm_desc.is_match(fm) {
                skill.lines.push(format!(
                    "{rel}: frontmatter has no `description:` — nothing to match on"
                ));
            }
        }
    }
    // Links are checked across SKILL.md AND its references: a dead
    // pointer in a reference file is exactly as dead.
    let mut docs = vec![(rel.clone(), text.to_owned())];
    if let Ok(refs) = std::fs::read_dir(ctx.root.join(name).join("references")) {
        let mut paths: Vec<_> = refs
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|e| e == "md"))
            .collect();
        paths.sort();
        for path in paths {
            let here = format!(
                "{name}/{}",
                path.strip_prefix(ctx.root.join(name)).map_or_else(
                    |_| path.to_string_lossy().into_owned(),
                    |r| r.to_string_lossy().into_owned()
                )
            );
            docs.push((here, std::fs::read_to_string(&path).unwrap_or_default()));
            skill.extra_files += 1;
        }
    }
    for (here, body) in &docs {
        audit_links(
            ctx.scanner,
            ctx.root,
            name,
            ctx.names,
            here,
            body,
            &mut skill.lines,
        );
    }
    match ctx.baseline.get(name).and_then(|entry| entry.words) {
        None => {
            if skill.words > ctx.ceiling {
                skill.lines.push(format!(
                    "{rel}: {} words > ceiling {}. Split the case studies into references/ (progressive disclosure), or seed a baseline entry with a reason if it genuinely must be large.",
                    skill.words, ctx.max_words
                ));
            }
        }
        Some(allowed) => {
            // Over the ceiling but within the baselined number: held, no
            // violation. Under the ceiling: the exemption is stale.
            if skill.words > ctx.ceiling {
                if skill.words > allowed {
                    skill.lines.push(format!(
                        "{rel}: {} words, up from a baselined {allowed}. The ratchet only shrinks.",
                        skill.words
                    ));
                }
            } else {
                skill.lines.push(format!(
                    "{rel}: {} words is under the ceiling but still baselined as oversized — delete the stale entry; a fixed violation must take its exemption with it.",
                    skill.words
                ));
            }
        }
    }
    skill.sections = ctx
        .scanner
        .section
        .captures_iter(text)
        .map(|caps| caps[1].trim().to_lowercase())
        .collect();
    skill
}

/// Wikilinks live in prose, not in literals. Mirrors `_prose`.
///
/// A `[[table]]` in backticks is a literal — TOML writes array-of-tables
/// exactly that way — and a fenced block is a quotation. Matching either
/// as a cross-skill pointer turned a case study's config names into two
/// dead-link failures that no edit to the skills could honestly fix.
/// Reflinks are Markdown links by construction, so they still read the
/// raw body, exactly like the reference.
pub(crate) fn prose(scanner: &Scanner, body: &str) -> String {
    let no_fences = scanner
        .fenced
        .replace_all(body, "")
        .into_owned();
    scanner.inline_code.replace_all(&no_fences, "").into_owned()
}

/// Cross-skill and reference-file links for one document, wikilinks
/// deduped per file like the reference's `set(findall)`.
pub(crate) fn audit_links(
    scanner: &Scanner,
    root: &std::path::Path,
    name: &str,
    names: &BTreeSet<&str>,
    here: &str,
    body: &str,
    lines: &mut Vec<String>,
) {
    let text = prose(scanner, body);
    let targets: BTreeSet<&str> = scanner
        .wikilink
        .captures_iter(&text)
        .map(|c| c.get(1).map_or("", |m| m.as_str()))
        .collect();
    for target in targets {
        if !names.contains(target) {
            lines.push(format!("{here}: [[{target}]] matches no skill"));
        }
    }
    let targets: BTreeSet<&str> = scanner
        .reflink
        .captures_iter(body)
        .map(|c| c.get(1).map_or("", |m| m.as_str()))
        .collect();
    for target in targets {
        if !root.join(name).join(target).exists() {
            lines.push(format!("{here}: link to {target} — file does not exist"));
        }
    }
}

/// LESSON-shaped headings owned by more than one skill. Structural
/// headings are short labels and never reach the length floor.
pub(crate) fn audit_duplicates(sections: &BTreeMap<String, Vec<String>>) -> Vec<String> {
    let mut lines = Vec::new();
    for (title, owners) in sections {
        if owners.len() > 1 && title.chars().count() >= LESSON_TITLE_CHARS {
            let shown: String = title.chars().take(TITLE_DISPLAY_CHARS).collect();
            let mut owners = owners.clone();
            owners.sort();
            lines.push(format!(
                "section \"{shown}\" appears in {} skills ({}) — one lesson, one home",
                owners.len(),
                owners.join(", ")
            ));
        }
    }
    lines
}

/// Re-record today's oversized set. Seeded reasons survive; new entries
/// are honestly `"unreviewed"`, never a silent excuse.
pub(crate) fn render_update(
    inputs: &AuditInputs,
    baseline: &BTreeMap<String, BaselineEntry>,
    measured: &BTreeMap<String, u64>,
    out: &mut String,
) -> AuditOutcome {
    use std::fmt::Write as _;
    let ceiling = u64::try_from(inputs.max_words).unwrap_or(u64::MAX);
    let mut over: BTreeMap<String, BTreeMap<String, serde_json::Value>> = BTreeMap::new();
    for (skill, words) in measured {
        if *words <= ceiling {
            continue;
        }
        let reason = baseline
            .get(skill)
            .and_then(|entry| entry.reason.clone())
            .unwrap_or_else(|| "unreviewed".to_owned());
        over.insert(
            skill.clone(),
            BTreeMap::from([
                ("words".to_owned(), serde_json::Value::from(*words)),
                ("reason".to_owned(), serde_json::Value::from(reason)),
            ]),
        );
    }
    let doc = serde_json::json!({"oversized": over});
    let text = serde_json::to_string_pretty(&doc).unwrap_or_default() + "\n";
    let _ = std::fs::write(&inputs.baseline, text);
    let _ = writeln!(
        out,
        "  → baseline re-recorded: {} oversized skill(s)",
        over.len()
    );
    AuditOutcome {
        code: 0,
        out: std::mem::take(out),
    }
}
