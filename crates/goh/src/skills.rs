//! Skills-corpus audit — Rust port of `checks/check_skills_corpus.py`.
//!
//! An agent skills corpus is code nothing compiles, so its defects are
//! silent: skills with no frontmatter (never triggerable), links to
//! skills that do not exist, unbounded `SKILL.md` growth, lessons
//! restated across skills. Six checks, each of which found a real defect
//! on its first run: frontmatter, name, wikilinks, reflinks, size (a
//! shrink-only ratchet, not a cap), duplicates.
//!
//! Word counting uses Unicode whitespace runs, like the reference's
//! `str.split()` — except the C0/C1 controls `\x1c`–`\x1f` and NEL,
//! which `str.split()` treats as breaks and `split_whitespace` does not.
//! No authored prose relies on those as word breaks — accepted, same
//! class as the earlier ports' line-splitting notes.

use std::collections::{BTreeMap, BTreeSet};

use crate::skills_audit::{audit_duplicates, audit_skill, render_update, SkillCtx};

/// Floor: fewer skills than this means the scope is wrong, not the
/// corpus clean. Mirrors `DEFAULT_MIN_SKILLS` (overridable by flag).
pub const DEFAULT_MIN_SKILLS: usize = 5;
/// Word ceiling for a new skill. Mirrors `DEFAULT_MAX_WORDS`
/// (overridable by flag).
pub const DEFAULT_MAX_WORDS: usize = 5000;
/// Baseline filename alongside the corpus root. Mirrors
/// `BASELINE_NAME`.
pub const BASELINE_NAME: &str = "skills_size_baseline.json";

/// Frontmatter `name:` line. Mirrors `FM_NAME`.
const FM_NAME: &str = r"(?m)^name:\s*(\S+)\s*$";
/// Frontmatter `description:` presence. Mirrors `FM_DESC`.
const FM_DESC: &str = r"(?m)^description:\s*\S";
/// Cross-skill links. Mirrors `WIKILINK`.
const WIKILINK: &str = r"\[\[([A-Za-z0-9_\-]+)\]\]";
/// Reference-file links. Mirrors `REFLINK`.
const REFLINK: &str = r"\]\((references/[^)]+\.md)\)";
/// Section headings. Mirrors `SECTION`.
const SECTION: &str = r"(?m)^##\s+(.+?)\s*$";

/// A LESSON heading is a sentence; a structural one (`Related`, `The
/// move`) is a label. Length separates them with no list to maintain.
/// Mirrors `LESSON_TITLE_CHARS`.
pub(crate) const LESSON_TITLE_CHARS: usize = 30;

/// Display width for a duplicated lesson title. Mirrors the
/// reference's `title[:60]`.
pub(crate) const TITLE_DISPLAY_CHARS: usize = 60;

/// Compiled corpus patterns.
pub struct Scanner {
    /// Frontmatter names.
    pub(crate) fm_name: regex::Regex,
    /// Frontmatter descriptions.
    pub(crate) fm_desc: regex::Regex,
    /// Cross-skill links.
    pub(crate) wikilink: regex::Regex,
    /// Reference-file links.
    pub(crate) reflink: regex::Regex,
    /// Section headings.
    pub(crate) section: regex::Regex,
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
            regex::Regex::new(pattern).map_err(|e| format!("built-in skills pattern failed: {e}"))
        }
        Ok(Self {
            fm_name: one(FM_NAME)?,
            fm_desc: one(FM_DESC)?,
            wikilink: one(WIKILINK)?,
            reflink: one(REFLINK)?,
            section: one(SECTION)?,
        })
    }
}

/// One violation line (the `  ✗ ` prefix is added at render).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Violation {
    /// Render-ready line without the prefix.
    pub text: String,
}

/// The frontmatter block, or `None` when the file has none. Strict
/// about the opening fence: a file merely CONTAINING `---` is not a
/// skill with frontmatter. Mirrors `_frontmatter`.
#[must_use]
pub fn frontmatter(text: &str) -> Option<&str> {
    if !text.starts_with("---\n") {
        return None;
    }
    let end = text[3..].find("\n---\n").map(|i| i + 3)?;
    Some(&text[4..end])
}

/// Skill directories: sorted, no dot-dirs, each holding a `SKILL.md`.
/// Mirrors `_skill_dirs`.
#[must_use]
pub fn skill_dirs(root: &std::path::Path) -> Vec<String> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    let mut names: Vec<String> = entries
        .filter_map(Result::ok)
        .filter(|e| {
            e.file_type().is_ok_and(|t| t.is_dir())
                && !e.file_name().to_string_lossy().starts_with('.')
                && e.path().join("SKILL.md").is_file()
        })
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// One seeded oversized entry.
///
/// Today's ceiling plus the reason it may stand (re-record preserves
/// seeded reasons, new entries are `"unreviewed"` — the honest state,
/// never a silent excuse).
#[derive(Debug, Clone, Default)]
pub struct BaselineEntry {
    /// Ceiling words, when the entry carries a measurable one.
    pub words: Option<u64>,
    /// Why the exemption may stand.
    pub reason: Option<String>,
}

/// Oversized baselines keyed by skill.
///
/// Missing file → no exemptions; UNREADABLE file → named error
/// (refusing, never falling back to "no exemptions", which would turn
/// every seeded entry into a fresh violation). Mirrors `_load_baseline`.
///
/// # Errors
///
/// Returns the refusal message when the baseline exists but does not
/// parse.
pub fn load_baseline(path: &std::path::Path) -> Result<BTreeMap<String, BaselineEntry>, String> {
    if !path.exists() {
        return Ok(BTreeMap::new());
    }
    let raw = std::fs::read_to_string(path)
        .map_err(|e| format!("baseline {} is unreadable: {e}", path.display()))?;
    let data: serde_json::Value = serde_json::from_str(&raw)
        .map_err(|e| format!("baseline {} is unreadable: {e}", path.display()))?;
    let mut out = BTreeMap::new();
    if let Some(oversized) = data.get("oversized").and_then(serde_json::Value::as_object) {
        for (name, entry) in oversized {
            out.insert(
                name.clone(),
                BaselineEntry {
                    words: entry.get("words").and_then(serde_json::Value::as_u64),
                    reason: entry
                        .get("reason")
                        .and_then(serde_json::Value::as_str)
                        .map(str::to_owned),
                },
            );
        }
    }
    Ok(out)
}

/// Audit inputs, straight from flags/config.
pub struct AuditInputs {
    /// Word ceiling for new skills.
    pub max_words: usize,
    /// Floor on the skill count (scope guard).
    pub min_skills: usize,
    /// Baseline path (already resolved).
    pub baseline: std::path::PathBuf,
    /// Re-record today's oversized set instead of auditing.
    pub update: bool,
}

/// Audit outcome: exit code plus the exact stdout text (the reference
/// prints EVERYTHING, including violations, to stdout).
pub struct AuditOutcome {
    /// Exit code.
    pub code: i32,
    /// Stdout text.
    pub out: String,
}

/// Audit one corpus.
///
/// Scope floor, per-skill stages, duplicates, then the update-or-verdict
/// render. Mirrors `check_corpus` branch for branch, including the
/// per-file wikilink dedup (`set(findall)`) and the stale-baseline
/// refusal inside the size check.
#[must_use]
pub fn audit(scanner: &Scanner, root: &std::path::Path, inputs: &AuditInputs) -> AuditOutcome {
    use std::fmt::Write as _;
    let skills = skill_dirs(root);
    if skills.len() < inputs.min_skills {
        return AuditOutcome {
            code: 2,
            out: format!(
                "  ✗ only {} skill(s) under {} (floor {}) — the scope is wrong, not the corpus clean\n",
                skills.len(),
                root.display(),
                inputs.min_skills
            ),
        };
    }
    let baseline = match load_baseline(&inputs.baseline) {
        Ok(baseline) => baseline,
        Err(message) => {
            return AuditOutcome {
                code: 2,
                out: format!("  ✗ {message}\n"),
            };
        }
    };
    let names: BTreeSet<&str> = skills.iter().map(String::as_str).collect();
    let ctx = SkillCtx {
        scanner,
        root,
        names: &names,
        baseline: &baseline,
        ceiling: u64::try_from(inputs.max_words).unwrap_or(u64::MAX),
        max_words: inputs.max_words,
    };
    let mut lines = Vec::new();
    let mut sections: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut measured = BTreeMap::new();
    let mut files_read = 0;
    for name in &skills {
        let text = std::fs::read_to_string(root.join(name).join("SKILL.md")).unwrap_or_default();
        files_read += 1;
        let mut skill = audit_skill(&ctx, name, &text);
        lines.append(&mut skill.lines);
        files_read += skill.extra_files;
        measured.insert(name.clone(), skill.words);
        for title in skill.sections {
            sections.entry(title).or_default().push(name.clone());
        }
    }
    lines.extend(audit_duplicates(&sections));
    let mut out = String::new();
    for line in &lines {
        let _ = writeln!(out, "  ✗ {line}");
    }
    if inputs.update {
        return render_update(inputs, &baseline, &measured, &mut out);
    }
    if lines.is_empty() {
        AuditOutcome {
            code: 0,
            out: format!(
                "  ✓ [skills_corpus] OK — {} skills, {files_read} files, largest SKILL.md {} words (ceiling {})\n",
                skills.len(),
                measured.values().max().unwrap_or(&0),
                inputs.max_words
            ),
        }
    } else {
        let _ = writeln!(
            out,
            "  ✗ [skills_corpus] {} violation(s) across {} skills, {files_read} files",
            lines.len(),
            skills.len()
        );
        AuditOutcome { code: 1, out }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scanner() -> Scanner {
        Scanner::compile().expect("built-in patterns compile")
    }

    fn skill(root: &std::path::Path, name: &str, body: &str) {
        let dir = root.join(name);
        std::fs::create_dir_all(&dir).expect("mkdir");
        std::fs::write(dir.join("SKILL.md"), body).expect("write");
    }

    fn minimal(name: &str) -> String {
        format!("---\nname: {name}\ndescription: d\n---\n# {name}\nBody text here.\n")
    }

    fn inputs(root: &std::path::Path) -> AuditInputs {
        AuditInputs {
            max_words: 50,
            min_skills: 1,
            baseline: root.join("skills_size_baseline.json"),
            update: false,
        }
    }

    #[test]
    fn clean_corpus_passes() {
        let dir = tempfile::tempdir().expect("tempdir");
        skill(dir.path(), "alpha", &minimal("alpha"));
        skill(dir.path(), "beta", &minimal("beta"));
        let outcome = audit(&scanner(), dir.path(), &inputs(dir.path()));
        assert_eq!(outcome.code, 0, "{}", outcome.out);
    }

    #[test]
    fn every_check_fires() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        // No frontmatter at all.
        skill(root, "bare", "Just prose, no fence.\n");
        // Name mismatch + dead wikilink + dead reflink + oversized.
        let big = "word ".repeat(60);
        skill(
            root,
            "messy",
            &format!(
                "---\nname: wrong\ndescription: d\n---\n# messy\nSee [[ghost]] and [doc](references/gone.md).\n{big}\n"
            ),
        );
        std::fs::create_dir_all(root.join("messy/references")).expect("mkdir");
        let outcome = audit(&scanner(), root, &inputs(root));
        assert_eq!(outcome.code, 1, "{}", outcome.out);
        for probe in [
            "no YAML frontmatter",
            "does not match directory",
            "matches no skill",
            "file does not exist",
            "words > ceiling",
        ] {
            assert!(outcome.out.contains(probe), "{probe}:\n{}", outcome.out);
        }
    }

    #[test]
    fn duplicate_lessons_fire_structural_headings_do_not() {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path();
        let lesson = "## A pipeline swallows the exit code you are checking\n";
        skill(
            root,
            "one",
            &format!("{}\n{lesson}\n## Related\n", minimal("one")),
        );
        skill(
            root,
            "two",
            &format!("{}\n{lesson}\n## Related\n", minimal("two")),
        );
        let outcome = audit(&scanner(), root, &inputs(root));
        assert_eq!(outcome.code, 1, "{}", outcome.out);
        assert!(
            outcome.out.contains("one lesson, one home"),
            "{}",
            outcome.out
        );
        assert!(!outcome.out.contains("Related"), "{}", outcome.out);
    }

    #[test]
    fn scope_floor_refuses() {
        let dir = tempfile::tempdir().expect("tempdir");
        let outcome = audit(
            &scanner(),
            dir.path(),
            &AuditInputs {
                max_words: 50,
                min_skills: 5,
                baseline: dir.path().join("b.json"),
                update: false,
            },
        );
        assert_eq!(outcome.code, 2);
        assert!(outcome.out.contains("floor 5"), "{}", outcome.out);
    }

    #[test]
    fn frontmatter_fence_is_strict() {
        assert!(frontmatter("---\nname: x\n---\nbody\n").is_some());
        assert!(frontmatter("prose\n---\nname: x\n---\n").is_none());
        assert!(frontmatter("---\nname: x\nno close\n").is_none());
    }
}
