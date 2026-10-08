//! Which `actions/checkout` steps leave the job token in git config, read from a file's YAML.
//!
//! A YAML parser decides what a step is (`saphyr`, with source spans): a `uses:` inside a `run: |`
//! block is script text, a flow-mapping step `{uses: ..., with: {...}}` is a step, and an alias
//! `with: *defaults` resolves to the mapping it names. Comments are not in the parse, so the
//! exemption marker is read from the step's own lines in the source (`region`).

use saphyr::{LoadableYamlNode, MarkedYaml, Scalar, YamlData};

/// The input that decides whether checkout leaves the token behind.
pub const KEY: &str = "persist-credentials";
/// The per-step exemption: `# persist-credentials-ok: <reason>`, on the step or directly above.
pub const MARKER: &str = "persist-credentials-ok:";

/// Is `rel` a file GitHub runs steps from: a workflow (`.github/workflows/*.yml|yaml`, which
/// GitHub reads only at the top level) or a composite action (`.github/actions/**/action.yml`).
#[must_use]
pub fn in_scope(rel: &str) -> bool {
    if let Some(name) = rel.strip_prefix(".github/workflows/") {
        let path = std::path::Path::new(name);
        return !name.contains('/')
            && path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("yml") || e.eq_ignore_ascii_case("yaml"));
    }
    rel.strip_prefix(".github/actions/").is_some_and(|rest| {
        let name = rest.rsplit('/').next().unwrap_or(rest);
        name == "action.yml" || name == "action.yaml"
    })
}

/// What is wrong with one checkout step (or the file).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// No `persist-credentials` at all: checkout's default is `true`.
    Unset,
    /// Set, to anything but the literal `false` (as written: `true`, an expression, `no`).
    NotFalse(String),
    /// The marker, with no reason after it.
    EmptyReason,
    /// The marker on a checkout that already sets `false`: it exempts nothing.
    StaleMarker,
    /// Not YAML the check can read -- nothing in it can be shown to drop the token.
    Unparsable(String),
}

/// One finding: the 1-based line, the step it names, and what is wrong.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub step: String,
    pub problem: Problem,
}

fn key_is(node: &MarkedYaml, want: &str) -> bool {
    matches!(&node.data, YamlData::Value(Scalar::String(s)) if s.eq_ignore_ascii_case(want))
}

/// `node[key]` when `node` is a mapping (key compared ignoring ASCII case, as GitHub does for
/// input names; structural keys are lowercase in every file GitHub accepts).
fn get<'a, 'i>(node: &'a MarkedYaml<'i>, key: &str) -> Option<&'a MarkedYaml<'i>> {
    match &node.data {
        YamlData::Mapping(m) => m.iter().find(|(k, _)| key_is(k, key)).map(|(_, v)| v),
        _ => None,
    }
}

fn as_str<'a>(node: &'a MarkedYaml) -> Option<&'a str> {
    match &node.data {
        YamlData::Value(Scalar::String(s)) => Some(s),
        _ => None,
    }
}

/// Every step list in a document, with whose it is: `jobs.<id>.steps` (a workflow) and
/// `runs.steps` (a composite action).
fn step_lists<'a, 'i>(doc: &'a MarkedYaml<'i>) -> Vec<(String, &'a [MarkedYaml<'i>])> {
    let mut out = Vec::new();
    if let Some(YamlData::Mapping(jobs)) = get(doc, "jobs").map(|j| &j.data) {
        for (id, job) in jobs {
            if let Some(YamlData::Sequence(steps)) = get(job, "steps").map(|s| &s.data) {
                let id = as_str(id).unwrap_or("?");
                out.push((format!("job `{id}`"), steps.as_slice()));
            }
        }
    }
    if let Some(runs) = get(doc, "runs") {
        if let Some(YamlData::Sequence(steps)) = get(runs, "steps").map(|s| &s.data) {
            out.push(("composite action".to_owned(), steps.as_slice()));
        }
    }
    out
}

/// `uses: actions/checkout@<ref>`, any ref (tag, branch, pinned sha); owner/repo ignore case.
fn is_checkout(step: &MarkedYaml) -> bool {
    get(step, "uses")
        .and_then(as_str)
        .and_then(|u| u.trim().split_once('@'))
        .is_some_and(|(action, _)| action.eq_ignore_ascii_case("actions/checkout"))
}

/// The literal `false`: the boolean, or the string `false` in any quoting and case (an action
/// input is a string either way). `no`, `0`, an expression and `true` are not.
fn is_literal_false(v: &MarkedYaml) -> bool {
    match &v.data {
        YamlData::Value(Scalar::Boolean(b)) => !b,
        YamlData::Value(Scalar::String(s)) => s.trim().eq_ignore_ascii_case("false"),
        _ => false,
    }
}

fn render(v: &MarkedYaml) -> String {
    match &v.data {
        YamlData::Value(Scalar::Boolean(b)) => b.to_string(),
        YamlData::Value(Scalar::String(s)) => s.to_string(),
        YamlData::Value(Scalar::Integer(i)) => i.to_string(),
        YamlData::Value(Scalar::FloatingPoint(f)) => f.to_string(),
        YamlData::Value(Scalar::Null) => "null".to_owned(),
        _ => "a non-scalar".to_owned(),
    }
}

fn indent(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

fn is_comment_line(line: &str) -> bool {
    line.trim_start().starts_with('#')
}

/// The comment on `line`, from its `#`.
///
/// A `#` at the line start or after a blank, outside a quoted scalar. A quote opens a scalar only
/// where one can start -- after `:`, `-`, `[`, `{`, `,`, or the indentation -- so
/// `name: Don't` holds no quoted scalar.
#[must_use]
pub fn comment_of(line: &str) -> Option<&str> {
    let mut quote: Option<char> = None;
    let mut prev: Option<char> = None;
    let mut last_sig: Option<char> = None;
    for (i, c) in line.char_indices() {
        if let Some(q) = quote {
            if c == q {
                quote = None;
            }
        } else if c == '#' && prev.is_none_or(char::is_whitespace) {
            return line.get(i..);
        } else if (c == '\'' || c == '"')
            && last_sig.is_none_or(|p| ":-[{,".contains(p))
            && prev.is_none_or(|p| p.is_whitespace() || "[{,".contains(p))
        {
            quote = Some(c);
        }
        if !c.is_whitespace() {
            last_sig = Some(c);
        }
        prev = Some(c);
    }
    None
}

/// The marker's reason in `lines` (1-based `from..=to`): `Some(Ok)` with a reason, `Some(Err)`
/// when only reasonless markers stand, `None` without one.
fn marker(lines: &[&str], from: usize, to: usize) -> Option<Result<String, ()>> {
    let mut seen = None;
    for line in lines.iter().take(to).skip(from.saturating_sub(1)) {
        let Some(comment) = comment_of(line) else {
            continue;
        };
        if let Some((_, reason)) = comment.split_once(MARKER) {
            let reason = reason.trim();
            if !reason.is_empty() {
                return Some(Ok(reason.to_owned()));
            }
            seen = Some(Err(()));
        }
    }
    seen
}

/// The source lines a step owns, 1-based inclusive: from its first line to its last line of
/// content (a trailing blank or a line dedented past the step's keys belongs to what follows),
/// widened upward over the comment-only lines directly above it at or left of its `-` (deeper
/// ones are a previous step's block-scalar text, not a comment on this step).
fn region(lines: &[&str], step: &MarkedYaml, next_start: Option<usize>) -> (usize, usize) {
    let start = step.span.start.line().max(1);
    let content_col = step.span.start.col();
    let bound = next_start
        .unwrap_or_else(|| step.span.end.line() + 1)
        .min(lines.len() + 1);
    let mut last = bound.saturating_sub(1).max(start);
    while last > start {
        let line = lines.get(last - 1).copied().unwrap_or("");
        if line.trim().is_empty() || indent(line) < content_col {
            last -= 1;
        } else {
            break;
        }
    }
    let first_line = lines.get(start - 1).copied().unwrap_or("");
    let dash_col = first_line
        .get(..content_col.min(first_line.len()))
        .and_then(|head| head.rfind('-'))
        .unwrap_or(content_col);
    let mut first = start;
    while first > 1 {
        let above = lines.get(first - 2).copied().unwrap_or("");
        if is_comment_line(above) && indent(above) <= dash_col {
            first -= 1;
        } else {
            break;
        }
    }
    (first, last)
}

fn label(owner: &str, index: usize, step: &MarkedYaml) -> String {
    let what = get(step, "name")
        .and_then(as_str)
        .or_else(|| get(step, "uses").and_then(as_str))
        .unwrap_or("?");
    format!("{owner}, step {} (`{}`)", index + 1, what.trim())
}

/// Judge one step: `None` when it drops its token (or keeps it with a reason).
fn judge_step(
    lines: &[&str],
    step: &MarkedYaml,
    next_start: Option<usize>,
) -> Option<(usize, Problem)> {
    let (first, last) = region(lines, step, next_start);
    let start = step.span.start.line();
    let value = get(step, "with").and_then(|w| get(w, KEY));
    let keeps = !value.is_some_and(is_literal_false);
    match (marker(lines, first, last), keeps) {
        (Some(Err(())), _) => Some((start, Problem::EmptyReason)),
        (Some(Ok(_)), true) | (None, false) => None,
        (Some(Ok(_)), false) => Some((start, Problem::StaleMarker)),
        (None, true) => Some(value.map_or((start, Problem::Unset), |v| {
            // An alias resolves to its anchor's line; name the step's own line then.
            let at = v.span.start.line();
            let at = if (start..=last).contains(&at) {
                at
            } else {
                start
            };
            (at, Problem::NotFalse(render(v)))
        })),
    }
}

/// Every finding in one file's text.
#[must_use]
pub fn findings(text: &str) -> Vec<Finding> {
    let docs = match MarkedYaml::load_from_str(text) {
        Ok(docs) => docs,
        Err(e) => {
            return vec![Finding {
                line: e.marker().line().max(1),
                step: String::new(),
                problem: Problem::Unparsable(e.to_string()),
            }]
        }
    };
    let lines: Vec<&str> = text.lines().collect();
    let mut out = Vec::new();
    for doc in &docs {
        for (owner, steps) in step_lists(doc) {
            for (i, step) in steps.iter().enumerate() {
                if !is_checkout(step) {
                    continue;
                }
                let next_start = steps.get(i + 1).map(|s| s.span.start.line());
                if let Some((line, problem)) = judge_step(&lines, step, next_start) {
                    out.push(Finding {
                        line,
                        step: label(&owner, i, step),
                        problem,
                    });
                }
            }
        }
    }
    out.sort_by_key(|f| f.line);
    out
}
