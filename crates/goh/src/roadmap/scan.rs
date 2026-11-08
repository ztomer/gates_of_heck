//! The roadmap's text -> its findings. Pure: a file's text in, line-numbered findings out.
//!
//! The shape the 2026-11-08 rework gave every roadmap (`~/Projects/.loop/audits/roadmaps-2026-11-08`)
//! and what this reads of it:
//!
//!   * open work sits under PHASE headings (`## Phase 1 — ...`, `### Phase 2: ...`): a heading whose
//!     text STARTS with `Phase` (after an optional section number). "Open items — in phase order"
//!     is not one; it holds them;
//!   * an ENTRY is a top-level list item, or a deeper heading that opens with an id, inside a
//!     phase, and runs to the next entry, heading or the end (a deeper heading with no id is a
//!     section, and ends the phase's entries). It must open with an id -- `RT.WINCLOSE`, `P0.4`,
//!     `H1` (a capital, then letters and digits, with a digit or a dot somewhere) or `ZoneWM`'s
//!     dotted `0.15` -- and carry a `Done when:` clause;
//!   * a `Done when:` clause that opens a line OUTSIDE every entry is a finding too: it is either
//!     open work the phases do not hold, or an entry shape this reader cannot see -- the anchor
//!     that keeps the entry grammar from going blind in silence (calibrate-the-instrument);
//!   * no ENTRY is marked completed -- a heading, list item (any depth) or table row that carries an
//!     id, with `DONE`, `shipped`, `closed`, `✓`, `~~` or a ticked `[x]` box on it. Prose is not
//!     judged: "what shipped is in CHANGELOG.md" describes the plan, it marks no entry. A row with
//!     no id is judged by its STATUS POSITION: a heading, list item or table cell that OPENS with
//!     `DONE`/`Shipped`/`Closed` (never `Done`: `Done when:` opens every exit clause), `✓`, `~~` or
//!     `[x]` (games' `| ... | DONE (22ab30b) |` rows). Completed work lives in git and the
//!     CHANGELOG (house rule #16). The one escape is `<!-- roadmap-ok: <reason> -->` on the line
//!     itself, with a reason;
//!   * `(queue: <ID>)` cites an umbrella queue item; the caller asks the queue which are done.
//!
//! Fenced code is not read, and inline code spans are blanked, so a roadmap may QUOTE a marker.

use regex::Regex;
use std::sync::LazyLock;

fn re(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
}

static COMPLETED: LazyLock<Regex> = LazyLock::new(|| {
    re(r"\bDONE\b|(?i:\bshipped\b|\bclosed\b)|✓|~~|^\s*(?:[-*+]|\d+[.)])\s+\[[xX]\]")
});
static ANY_ITEM: LazyLock<Regex> = LazyLock::new(|| re(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$"));
static STATUS_FIRST: LazyLock<Regex> = LazyLock::new(|| {
    re(r"^[*_`\s]*(?:\[[xX]\]|✓|~~|(?:DONE|CLOSED|SHIPPED|Closed|Shipped)(?:$|[\s(.,;:)*]))")
});
static TABLE_SEP: LazyLock<Regex> = LazyLock::new(|| re(r"^[\s|:-]*$"));
static JUSTIFIED: LazyLock<Regex> = LazyLock::new(|| re(r"<!--\s*roadmap-ok:\s*(\S[^>]*?)\s*-->"));
static HEADING: LazyLock<Regex> = LazyLock::new(|| re(r"^(#{1,6})\s+(.*?)\s*#*\s*$"));
static PHASE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)^(?:[\d.]+\s+)?phase\b"));
static ITEM: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:[-*+]|\d+[.)])\s+(.*)$"));
static LEAD: LazyLock<Regex> = LazyLock::new(|| re(r"^(?:\[[ xX]\]\s+)?[*_`~✓\s]*"));
static ID: LazyLock<Regex> =
    LazyLock::new(|| re(r"^(?:[A-Z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*|\d+(?:\.\d+)+)\b"));
static CLAUSE: LazyLock<Regex> = LazyLock::new(|| re(r"(?i)done when\s*[*_]*\s*:"));
static CLAUSE_OPENS: LazyLock<Regex> =
    LazyLock::new(|| re(r"(?i)^\s*(?:[-*+]\s+)?[*_]{0,2}done when\b"));
static QUEUE: LazyLock<Regex> =
    LazyLock::new(|| re(r"\(queue:\s*([A-Z][A-Za-z0-9]*(?:[.\-][A-Za-z0-9]+)*)"));

/// The most phases a roadmap holds (the rework brief, rule 5).
pub const MAX_PHASES: usize = 5;

/// What a finding is, and the seed key's suffix.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Rule {
    /// A second plan file beside the declared one.
    Extra,
    /// A line marking completed work.
    Completed,
    /// An open entry with no id.
    NoId,
    /// An open entry with no `Done when:` clause.
    NoDoneWhen,
    /// A `Done when:` clause outside every entry.
    Orphan,
    /// A `(queue: <ID>)` whose umbrella item is done.
    QueueDone,
    /// A phase past the fifth.
    Phases,
}

impl Rule {
    /// The seed key's suffix, `<file>#<suffix>`.
    #[must_use]
    pub const fn key(self) -> &'static str {
        match self {
            Self::Extra => "extra-roadmap",
            Self::Completed => "completed",
            Self::NoId => "no-id",
            Self::NoDoneWhen => "no-done-when",
            Self::Orphan => "orphan-done-when",
            Self::QueueDone => "queue-done",
            Self::Phases => "phases",
        }
    }
}

/// One finding: the rule, the 1-based line, and what was seen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub rule: Rule,
    pub line: usize,
    pub what: String,
}

/// One open entry, as the phase grammar read it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub line: usize,
    pub id: Option<String>,
    pub done_when: bool,
}

/// What one roadmap's text holds.
#[derive(Debug, Default)]
pub struct Read {
    pub findings: Vec<Finding>,
    pub entries: Vec<Entry>,
    pub phases: usize,
    /// Every `(queue: <ID>)`, as (line, id), for the caller to ask the queue about.
    pub citations: Vec<(usize, String)>,
}

/// Is `rel` a plan file?
///
/// A `ROADMAP`, `BACKLOG`, `TODO` or `PLAN(S)` `.md` (the name whole, any case, or an UPPER-CASE name with that word between `_`/`-`: `FEATURE_PLAN.md`), or any `.md`
/// under a `plans/` or `roadmap(s)/` directory. A lower-case `weekend_plan.md` or `timeplan.md` is
/// a document about a plan, not the repo's plan.
#[must_use]
pub fn is_plan_file(rel: &str) -> bool {
    const WORDS: [&str; 5] = ["ROADMAP", "BACKLOG", "TODO", "PLAN", "PLANS"];
    let mut parts: Vec<&str> = rel.split('/').collect();
    let name = parts.pop().unwrap_or_default();
    let Some(stem) = name
        .strip_suffix(".md")
        .or_else(|| name.strip_suffix(".MD"))
    else {
        return false;
    };
    if parts.iter().any(|d| {
        let d = d.to_ascii_lowercase();
        d == "plans" || d == "roadmap" || d == "roadmaps"
    }) {
        return true;
    }
    let upper = stem.to_ascii_uppercase();
    if WORDS.contains(&upper.as_str()) {
        return true;
    }
    stem == upper && stem.split(['_', '-']).any(|w| WORDS.contains(&w))
}

/// The text a rule may read: fenced code blanked (line count kept), inline code spans blanked.
fn readable(text: &str) -> Vec<String> {
    let fences = crate::mdtext::Fences::new()
        .unwrap_or_else(|e| unreachable!("the house markdown grammar compiles: {e}"));
    crate::mdtext::splitlines(&fences.strip(text, false))
        .into_iter()
        .map(crate::mdtext::blank_code_spans)
        .collect()
}

/// The marker a justified line carries, if any, with its reason.
fn justified(raw: &str) -> bool {
    JUSTIFIED.is_match(raw)
}

/// The id an entry's title opens with, if it opens with one.
fn entry_id(title: &str) -> Option<String> {
    let rest = LEAD.replace(title, "");
    let id = ID.find(&rest)?.as_str();
    (id.contains('.') || id.bytes().any(|b| b.is_ascii_digit())).then(|| id.to_owned())
}

/// Is this line an ENTRY's: a heading, list item (any depth) or table row that carries an id?
/// Rule 2 judges these only: "What shipped is in CHANGELOG.md" is prose about the plan, not an
/// entry marked done (ztools `e556eb5`, three such lines, coordinator 2026-11-08).
fn carries_id(line: &str) -> bool {
    if let Some((_, text)) = heading(line) {
        return entry_id(text).is_some();
    }
    if let Some(c) = ANY_ITEM.captures(line) {
        return entry_id(c.get(1).map_or("", |m| m.as_str())).is_some();
    }
    let row = line.trim();
    row.starts_with('|')
        && !TABLE_SEP.is_match(row)
        && row.split('|').any(|cell| entry_id(cell.trim()).is_some())
}

/// A heading, list item or table cell that OPENS with a completion mark, id or not: `- [x] ...`,
/// `| ... | DONE (abc123) |`, `### ~~Old~~`. A status in the status position marks a row; the
/// same word inside a sentence is prose (`| Column | shipped-work text |` passes).
fn status_first(line: &str) -> Option<String> {
    let first = |t: &str| STATUS_FIRST.find(t).map(|m| m.as_str().to_owned());
    if let Some(c) = HEADING.captures(line) {
        return c.get(2).and_then(|t| first(t.as_str()));
    }
    if let Some(c) = ANY_ITEM.captures(line) {
        return c.get(1).and_then(|t| first(t.as_str()));
    }
    let row = line.trim();
    if !row.starts_with('|') || TABLE_SEP.is_match(row) {
        return None;
    }
    row.split('|').find_map(first)
}

/// The heading level and text of a line, if it is an ATX heading.
fn heading(line: &str) -> Option<(usize, &str)> {
    let c = HEADING.captures(line)?;
    Some((c.get(1)?.as_str().len(), c.get(2)?.as_str()))
}

/// Read one roadmap's text.
#[must_use]
pub fn read(text: &str) -> Read {
    let raw_lines = crate::mdtext::splitlines(text);
    let lines = readable(text);
    let mut out = Read::default();
    for (i, line) in lines.iter().enumerate() {
        let raw = raw_lines.get(i).copied().unwrap_or_default();
        if justified(raw) {
            continue;
        }
        let mark = if carries_id(line) {
            COMPLETED.find(line).map(|m| m.as_str().to_owned())
        } else {
            status_first(line)
        };
        if let Some(m) = mark {
            out.findings.push(Finding {
                rule: Rule::Completed,
                line: i + 1,
                what: format!(
                    "`{}` marks this entry completed: prune it (git and the CHANGELOG keep it), \
                     or justify the line with <!-- roadmap-ok: <reason> -->",
                    m.trim_matches(|c: char| c.is_whitespace() || "*_`(.,;:)".contains(c))
                ),
            });
        }
    }
    entries(&lines, &mut out);
    let joined = lines.join("\n");
    for c in QUEUE.captures_iter(&joined) {
        if let (Some(all), Some(id)) = (c.get(0), c.get(1)) {
            let line = joined[..all.start()].matches('\n').count() + 1;
            out.citations.push((line, id.as_str().to_owned()));
        }
    }
    out.findings.sort_by_key(|f| (f.line, f.rule));
    out
}

/// The phase grammar: entries, their ids and clauses, orphan clauses and the phase count.
fn entries(lines: &[String], out: &mut Read) {
    // (start line index, title) of the entry being read, and the level of the phase holding it.
    let mut phase: Option<usize> = None;
    let mut open: Option<(usize, String)> = None;
    // A heading entry holds the list under it; only a phase's own list opens entries.
    let mut heading_entry = false;
    let mut spans: Vec<(usize, usize, String)> = Vec::new();
    let close = |open: &mut Option<(usize, String)>, end: usize, spans: &mut Vec<_>| {
        if let Some((start, title)) = open.take() {
            spans.push((start, end, title));
        }
    };
    for (i, line) in lines.iter().enumerate() {
        if let Some((level, text)) = heading(line) {
            close(&mut open, i, &mut spans);
            if PHASE.is_match(text) {
                phase = Some(level);
                out.phases += 1;
                if out.phases > MAX_PHASES {
                    out.findings.push(Finding {
                        rule: Rule::Phases,
                        line: i + 1,
                        what: format!(
                            "phase {} of at most {MAX_PHASES}: order open work into five phases",
                            out.phases
                        ),
                    });
                }
            } else if phase.is_some_and(|p| level <= p) || entry_id(text).is_none() {
                // A sibling section, or a deeper one that is not an entry (`5.5 Standing
                // hygiene`): the phase's entries end. A clause under it is an orphan.
                phase = None;
            } else if phase.is_some() {
                open = Some((i, text.to_owned()));
            }
            heading_entry = open.is_some();
            continue;
        }
        if phase.is_none() || heading_entry {
            continue;
        }
        if let Some(c) = ITEM.captures(line) {
            close(&mut open, i, &mut spans);
            open = Some((i, c.get(1).map_or("", |m| m.as_str()).to_owned()));
        }
    }
    close(&mut open, lines.len(), &mut spans);
    let mut inside = vec![false; lines.len()];
    for (start, end, title) in spans {
        inside[start..end].iter_mut().for_each(|b| *b = true);
        let body = lines[start..end].join("\n");
        let entry = Entry {
            line: start + 1,
            id: entry_id(&title),
            done_when: CLAUSE.is_match(&body),
        };
        let short: String = title.chars().take(60).collect();
        if entry.id.is_none() {
            out.findings.push(Finding {
                rule: Rule::NoId,
                line: entry.line,
                what: format!("open entry `{short}` opens with no id (`AB.12`, `P1.3`, `H1`)"),
            });
        }
        if !entry.done_when {
            out.findings.push(Finding {
                rule: Rule::NoDoneWhen,
                line: entry.line,
                what: format!(
                    "open entry `{short}` has no `Done when:` clause (the test, command or \
                     observable that closes it; `Done when: (owner to decide)` if none is known)"
                ),
            });
        }
        out.entries.push(entry);
    }
    for (i, line) in lines.iter().enumerate() {
        if !inside[i] && CLAUSE_OPENS.is_match(line) {
            out.findings.push(Finding {
                rule: Rule::Orphan,
                line: i + 1,
                what: "a `Done when:` clause outside every phase entry: open work belongs under a \
                       `Phase` heading, as a list item or heading that opens with its id"
                    .to_owned(),
            });
        }
    }
}
