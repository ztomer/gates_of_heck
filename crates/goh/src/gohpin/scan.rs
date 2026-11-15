//! What one workflow file says about the goh it runs and the clone it runs it on, read from its YAML.
//!
//! Pure: the file's text, the running goh's version, whether the repository keeps a changelog, and
//! a reader for the repository's other files (the one level a `run:` is followed into) are the
//! inputs; the findings are the output.

use crate::checkoutcreds::scan::{as_str, get, step_lists};
use regex::Regex;
use saphyr::{LoadableYamlNode, MarkedYaml, Scalar, YamlData};
use std::sync::LazyLock;

/// How many minor releases a pin may trail the running goh by before it is stale.
///
/// goh ship's install step moves every consumer's pin after each release (`goh wf-pin --bump`, GOH.PINPROPAGATE);
/// a repository that sweep set aside (dirty, on hold, an owner's commit unpushed) keeps passing for
/// this many releases, then its own gate refuses it.
pub const WINDOW: u64 = 10;

/// What reads a repository's `v*` tags in a goh gate, as a command (never a prose mention: monitor's
/// `tools/ratchet_check.py` names `structural.sh` in a docstring): `goh changelog-tags`, run by the
/// structural layer, run by `gates/local_ci.sh` (`GOH_CI_STEPS`) and every house `tools/gate.sh`.
static READER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"gates/structural\.sh|gates/local_ci\.sh|\bgoh(?:\.sh)?"?\s+(?:structural|changelog-tags)\b"#)
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static TAG: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^v(\d+)\.(\d+)\.(\d+)$")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static GOH_REPO: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\s*[\w.-]+/gates_of_heck\s*$")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static CLONE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\bgit\s+clone\b.*github\.com[/:][\w.-]+/gates_of_heck\b")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static BRANCH: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"(?:--branch|-b)[ =]['"]?(v\d+\.\d+\.\d+)['"]?(?:\s|$)"#)
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});
static MAKE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\bmake\s+(?:-\S+\s+)*([\w.-]+)")
        .unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
});

/// A release `vX.Y.Z`.
pub type Version = (u64, u64, u64);

/// `vX.Y.Z` (or `X.Y.Z`) as numbers.
#[must_use]
pub fn version(s: &str) -> Option<Version> {
    let s = s.trim();
    let tagged = if s.starts_with('v') {
        s.to_owned()
    } else {
        format!("v{s}")
    };
    let c = TAG.captures(&tagged)?;
    let n = |i: usize| c.get(i).and_then(|m| m.as_str().parse().ok());
    Some((n(1)?, n(2)?, n(3)?))
}

/// Is a pin on `pin` older than the running goh `own` by more than `WINDOW` minor releases (or a
/// major)? A pin newer than the running goh is not stale: that goh is the old one.
#[must_use]
pub const fn is_stale(pin: Version, own: Version) -> bool {
    pin.0 < own.0 || (pin.0 == own.0 && pin.1 + WINDOW < own.1)
}

/// What is wrong.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// goh checked out or cloned at no named release (its default branch, a branch, a commit).
    Unpinned(String),
    /// goh pinned to a release more than `WINDOW` minors behind the running goh.
    Stale(String),
    /// A job that runs a tag-reading gate on a clone that holds no tags (the job's id).
    Shallow(String),
    /// Not YAML this check can read.
    Unparsable(String),
}

/// One finding: the 1-based line and what is wrong.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub problem: Problem,
}

fn render(v: &MarkedYaml) -> Option<String> {
    match &v.data {
        YamlData::Value(Scalar::String(s)) => Some(s.to_string()),
        YamlData::Value(Scalar::Integer(i)) => Some(i.to_string()),
        _ => None,
    }
}

fn line_of(v: &MarkedYaml) -> usize {
    v.span.start.line().max(1)
}

/// A pin as written: a release judged against `own`, a `GOH_TAG` reference (judged where
/// `GOH_TAG` is set), or anything else (unpinned, saying what it is).
fn judge_pin(line: usize, value: &str, own: Version, what: &str) -> Option<Finding> {
    if value.contains("GOH_TAG") {
        return None;
    }
    let problem = match version(value) {
        Some(pin) if value.trim().starts_with('v') => {
            if !is_stale(pin, own) {
                return None;
            }
            Problem::Stale(format!(
                "{what} pins goh {} -- the running goh is v{}.{}.{}, more than {WINDOW} minor \
                 releases on",
                value.trim(),
                own.0,
                own.1,
                own.2
            ))
        }
        _ => Problem::Unpinned(format!(
            "{what} `{}` is not a goh release tag (vX.Y.Z)",
            value.trim()
        )),
    };
    Some(Finding { line, problem })
}

/// Every `GOH_TAG:` under an `env:` mapping of `node`.
fn env_pins(node: &MarkedYaml, own: Version, out: &mut Vec<Finding>) {
    if let Some(v) = get(node, "env").and_then(|e| get(e, "GOH_TAG")) {
        let value = render(v).unwrap_or_default();
        out.extend(judge_pin(line_of(v), &value, own, "`GOH_TAG:`"));
    }
}

/// The pins in one step: a checkout of goh (`repository:` + `ref:`) or a `git clone` of it.
fn step_pins(step: &MarkedYaml, lines: &[&str], own: Version, out: &mut Vec<Finding>) {
    env_pins(step, own, out);
    let with = get(step, "with");
    if let Some(repo) = with.and_then(|w| get(w, "repository")).and_then(as_str) {
        if GOH_REPO.is_match(repo) {
            let at = line_of(step);
            match with.and_then(|w| get(w, "ref")) {
                None => out.push(Finding {
                    line: at,
                    problem: Problem::Unpinned(
                        "checks out goh with no `ref:`, so its default branch".to_owned(),
                    ),
                }),
                Some(r) => out.extend(judge_pin(
                    line_of(r),
                    &render(r).unwrap_or_default(),
                    own,
                    "`ref:`",
                )),
            }
        }
    }
    let Some(run) = get(step, "run").and_then(as_str) else {
        return;
    };
    let from = line_of(step);
    for cmd in run.lines() {
        let code = cmd.trim_start();
        if code.starts_with('#') || !CLONE.is_match(code) {
            continue;
        }
        let at = lines
            .iter()
            .enumerate()
            .skip(from - 1)
            .find(|(_, l)| l.contains(code.trim_end()))
            .map_or(from, |(i, _)| i + 1);
        if let Some(c) = BRANCH.captures(code) {
            out.extend(judge_pin(at, &c[1], own, "`git clone --branch`"));
        } else if !code.contains("GOH_TAG") {
            out.push(Finding {
                line: at,
                problem: Problem::Unpinned(
                    "clones goh with no `--branch vX.Y.Z`, so its default branch".to_owned(),
                ),
            });
        }
    }
}

/// Does `text` run a tag-reading gate: directly, or through one tracked script or Make target it
/// names (`tools/gate.sh --full`, `make ci`), whose text `read` returns.
#[must_use]
pub fn reaches_reader(text: &str, read: &dyn Fn(&str) -> Option<String>) -> bool {
    let runs = |body: &str| {
        body.lines()
            .any(|l| !l.trim_start().starts_with('#') && READER.is_match(l))
    };
    if runs(text) {
        return true;
    }
    let script = |word: &str| {
        let w = word.trim_matches(|c| c == '"' || c == '\'' || c == ';');
        let w = w.strip_prefix("./").unwrap_or(w);
        !w.is_empty()
            && !w.contains('$')
            && (w.contains('/')
                || std::path::Path::new(w)
                    .extension()
                    .is_some_and(|e| e.eq_ignore_ascii_case("sh") || e.eq_ignore_ascii_case("py")))
            && read(w).is_some_and(|body| runs(&body))
    };
    if text.split_whitespace().any(script) {
        return true;
    }
    MAKE.captures_iter(text)
        .any(|c| read("Makefile").is_some_and(|mk| runs(&recipe(&mk, &c[1]))))
}

/// The recipe lines of Make target `target` in `makefile`.
fn recipe(makefile: &str, target: &str) -> String {
    let mut out = String::new();
    let mut inside = false;
    for line in makefile.lines() {
        if line.starts_with('\t') {
            if inside {
                out.push_str(line);
                out.push('\n');
            }
            continue;
        }
        inside = line
            .split_once(':')
            .is_some_and(|(names, _)| names.split_whitespace().any(|n| n == target));
    }
    out
}

/// `fetch-depth: 0` on a checkout: the whole history, every tag (actions/checkout).
fn full_depth(step: &MarkedYaml) -> bool {
    get(step, "with")
        .and_then(|w| get(w, "fetch-depth"))
        .and_then(render)
        .is_some_and(|d| d.trim() == "0")
}

/// The checkout of the repository itself: `actions/checkout` with no other `repository:`.
fn is_own_checkout(step: &MarkedYaml) -> bool {
    let checkout = get(step, "uses")
        .and_then(as_str)
        .and_then(|u| u.trim().split_once('@'))
        .is_some_and(|(a, _)| a.eq_ignore_ascii_case("actions/checkout"));
    let other = get(step, "with")
        .and_then(|w| get(w, "repository"))
        .and_then(as_str)
        .is_some_and(|r| !r.contains("github.repository"));
    checkout && !other
}

/// The shallow finding for one job, if it runs a tag reader on a tagless clone of its repository.
fn job_depth(
    id: &str,
    steps: &[MarkedYaml],
    read: &dyn Fn(&str) -> Option<String>,
) -> Option<Finding> {
    let runs: String = steps
        .iter()
        .filter_map(|s| get(s, "run").and_then(as_str))
        .collect::<Vec<_>>()
        .join("\n");
    if !reaches_reader(&runs, read) {
        return None;
    }
    let own = steps.iter().find(|s| is_own_checkout(s))?;
    (!full_depth(own)).then(|| Finding {
        line: line_of(own),
        problem: Problem::Shallow(id.to_owned()),
    })
}

/// Every finding in one workflow's text. `changelog`: the repository keeps one, so a tag-reading
/// gate has something to judge; `read`: a tracked file's text, for the one level a `run:` names.
#[must_use]
pub fn findings(
    text: &str,
    own: Version,
    changelog: bool,
    read: &dyn Fn(&str) -> Option<String>,
) -> Vec<Finding> {
    let docs = match MarkedYaml::load_from_str(text) {
        Ok(docs) => docs,
        Err(e) => {
            return vec![Finding {
                line: e.marker().line().max(1),
                problem: Problem::Unparsable(e.to_string()),
            }]
        }
    };
    let lines: Vec<&str> = text.lines().collect();
    let mut out = Vec::new();
    for doc in &docs {
        env_pins(doc, own, &mut out);
        if let Some(YamlData::Mapping(jobs)) = get(doc, "jobs").map(|j| &j.data) {
            for (id, job) in jobs {
                env_pins(job, own, &mut out);
                let steps = match get(job, "steps").map(|s| &s.data) {
                    Some(YamlData::Sequence(steps)) => steps.as_slice(),
                    _ => continue,
                };
                if changelog {
                    out.extend(job_depth(as_str(id).unwrap_or("?"), steps, read));
                }
            }
        }
        for (_, steps) in step_lists(doc) {
            for step in steps {
                step_pins(step, &lines, own, &mut out);
            }
        }
    }
    out.sort_by_key(|f| f.line);
    out.dedup();
    out
}
