//! `goh requires-call`: a Python file that CALLS X must also CALL Y (BACKLOG 1.2, from `ZoneWM`).
//!
//! `ZoneWM` grew two repo-local gates of this one shape -- a tool that relaunches the agent must
//! judge window placement (`window_placement.guarded` or `.verify`); a long runner must keep
//! calling `.check()` -- so it is generic tooling, configured as rows in a TOML file named by
//! `GOH_REQUIRES_CALL` (or `--rules`):
//!
//! ```toml
//! [[rule]]
//! name = "placement"
//! why = "a relaunch can move the owner's windows"   # printed with every violation
//! files = ["tools/*.py"]                            # repo-relative globs
//! calls = ["relaunch"]       # the trigger; omit it and every matched file is bound
//! must_call = ["window_placement.guarded", "window_placement.verify"]
//! [rule.exempt]
//! "tools/census_round_controls.py" = "relaunch is stubbed: no agent restarts"
//! ```
//!
//! A name with a dot is `module.function`, resolved through the file's own imports (another
//! module's `verify` is not this one's); a bare name is any call whose last name it is. Read from
//! the AST (`requires_call_py`), because `ZoneWM`'s first regex version passed with the call
//! deleted -- its docstring named it. An exemption goes STALE, and fails, when its file is gone,
//! no longer calls X, or now calls Y. A rule that finds nothing to judge -- no file calls X, or no
//! file matches -- is a floor (exit 2), never a pass: the pattern moved.

use std::collections::BTreeMap;
use std::path::Path;

use crate::requires_call_py::{calls, Site};

/// One `[[rule]]`.
#[derive(Debug, Clone)]
pub struct Rule {
    pub name: String,
    pub why: String,
    pub files: Vec<String>,
    pub calls: Vec<String>,
    pub must_call: Vec<String>,
    pub exempt: BTreeMap<String, String>,
}

fn strings(t: &toml::Table, key: &str, rule: &str, required: bool) -> Result<Vec<String>, String> {
    let Some(v) = t.get(key) else {
        return if required {
            Err(format!("rule '{rule}' has no `{key}`"))
        } else {
            Ok(Vec::new())
        };
    };
    let list: Option<Vec<String>> = v
        .as_array()
        .map(|a| a.iter().map(|x| x.as_str().map(str::to_owned)).collect())
        .and_then(|x: Option<Vec<String>>| x);
    match list {
        Some(l) if !l.is_empty() || !required => Ok(l),
        _ => Err(format!(
            "rule '{rule}': `{key}` is a non-empty list of strings"
        )),
    }
}

/// Parse a rules file.
///
/// # Errors
///
/// A message naming what is missing or mistyped; the caller adds the file.
pub fn parse_rules(text: &str) -> Result<Vec<Rule>, String> {
    let table: toml::Table = text.parse().map_err(|e: toml::de::Error| e.to_string())?;
    let rules = table
        .get("rule")
        .and_then(toml::Value::as_array)
        .ok_or("no [[rule]] in it")?;
    rules
        .iter()
        .map(|r| {
            let t = r.as_table().ok_or("a [[rule]] that is not a table")?;
            let name = t
                .get("name")
                .and_then(toml::Value::as_str)
                .ok_or("a [[rule]] with no `name`")?
                .to_owned();
            let why = t
                .get("why")
                .and_then(toml::Value::as_str)
                .filter(|w| !w.trim().is_empty())
                .ok_or_else(|| format!("rule '{name}' has no `why` (printed with each violation)"))?
                .to_owned();
            let mut exempt = BTreeMap::new();
            if let Some(e) = t.get("exempt") {
                let e = e
                    .as_table()
                    .ok_or_else(|| format!("rule '{name}': `exempt` is a table"))?;
                for (file, reason) in e {
                    let reason = reason
                        .as_str()
                        .filter(|r| !r.trim().is_empty())
                        .ok_or_else(|| format!("rule '{name}': exemption {file} has no reason"))?;
                    exempt.insert(file.clone(), reason.to_owned());
                }
            }
            Ok(Rule {
                files: strings(t, "files", &name, true)?,
                calls: strings(t, "calls", &name, false)?,
                must_call: strings(t, "must_call", &name, true)?,
                exempt,
                why,
                name,
            })
        })
        .collect()
}

/// A glob over repo-relative paths: `*` and `?` stay within a directory, `**` crosses them.
fn glob_matches(glob: &str, path: &str) -> bool {
    let mut re = String::from("^");
    let mut chars = glob.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' if chars.peek() == Some(&'*') => {
                chars.next();
                if chars.peek() == Some(&'/') {
                    chars.next();
                    re.push_str("(?:.*/)?");
                } else {
                    re.push_str(".*");
                }
            }
            '*' => re.push_str("[^/]*"),
            '?' => re.push_str("[^/]"),
            c => re.push_str(&regex::escape(&c.to_string())),
        }
    }
    re.push('$');
    regex::Regex::new(&re).is_ok_and(|r| r.is_match(path))
}

/// What one rule found.
#[derive(Debug, Default)]
pub struct Verdict {
    /// Lines naming each violation, stale exemption and unreadable file.
    pub problems: Vec<String>,
    /// Files the rule bound (called X, or matched a file-only rule), exempt ones included.
    pub bound: usize,
    /// A floor: the rule found nothing to judge.
    pub empty: bool,
}

fn first<'s>(sites: &'s [Site], specs: &[String]) -> Option<&'s Site> {
    sites
        .iter()
        .find(|s| specs.iter().any(|spec| s.matches(spec)))
}

/// Judge one rule over `files` (repo-relative path, source) -- every file the repo lists.
#[must_use]
pub fn judge(rule: &Rule, files: &[(String, String)]) -> Verdict {
    let mut v = Verdict::default();
    let tag = format!("{}:", rule.name);
    let wanted = rule.must_call.join(" or ");
    let trigger = rule.calls.join(" or ");
    let matched: Vec<&(String, String)> = files
        .iter()
        .filter(|(p, _)| rule.files.iter().any(|g| glob_matches(g, p)))
        .collect();
    for (path, source) in &matched {
        let sites = match calls(source) {
            Ok(s) => s,
            Err(e) => {
                v.problems
                    .push(format!("{tag} {path}: does not parse ({e})"));
                continue;
            }
        };
        let hit = first(&sites, &rule.calls);
        let bound = rule.calls.is_empty() || hit.is_some();
        let judged = first(&sites, &rule.must_call).is_some();
        if let Some(reason) = rule.exempt.get(path.as_str()) {
            if !bound {
                v.problems.push(format!(
                    "{tag} {path}: stale exemption -- no longer calls {trigger} ({reason})"
                ));
            } else if judged {
                v.problems.push(format!(
                    "{tag} {path}: stale exemption -- now calls {wanted} itself"
                ));
            }
        }
        if !bound {
            continue;
        }
        v.bound += 1;
        if judged || rule.exempt.contains_key(path.as_str()) {
            continue;
        }
        v.problems.push(hit.map_or_else(
            || format!("{tag} {path}: never calls {wanted} -- {}", rule.why),
            |site| {
                format!(
                    "{tag} {path}:{}: calls {} and never calls {wanted} -- {}",
                    site.line, site.last, rule.why
                )
            },
        ));
    }
    for path in rule.exempt.keys() {
        if !matched.iter().any(|(p, _)| p == path) {
            v.problems.push(format!(
                "{tag} {path}: stale exemption -- no such file in {}",
                rule.files.join(" ")
            ));
        }
    }
    v.empty = v.bound == 0;
    v
}

/// `goh requires-call [--rules FILE] [ROOT]`.
#[must_use]
pub fn run(rules: Option<&str>, root: Option<&str>) -> i32 {
    let root = Path::new(root.unwrap_or("."));
    let rules = rules.map(str::to_owned).or_else(|| {
        crate::gatesrc::load(root)
            .ok()
            .and_then(|c| c.requires_call)
    });
    let Some(rules) = rules else {
        eprintln!(
            "✗ [requires_call] no rules: set GOH_REQUIRES_CALL in .gatesrc, or pass --rules FILE"
        );
        return 2;
    };
    check(root, &rules, false)
}

/// The check over `root` with the rules file `rules_path` (repo-relative or absolute); `staged`
/// reads the index. Prints its verdict; returns the exit code.
#[must_use]
pub fn check(root: &Path, rules_path: &str, staged: bool) -> i32 {
    let (code, good, bad) = evaluate(root, rules_path, staged);
    for line in &good {
        println!("✓ [requires_call] {line}");
    }
    for line in &bad {
        eprintln!("✗ [requires_call] {line}");
    }
    code
}

/// `(exit code, passing rules' lines, problem lines)` -- the structural step reports these.
#[must_use]
pub fn evaluate(root: &Path, rules_path: &str, staged: bool) -> (i32, Vec<String>, Vec<String>) {
    let text = crate::gitutil::content_bytes(root, rules_path, staged)
        .map(|b| String::from_utf8_lossy(&b).into_owned());
    let rules = match text.map_or_else(|| Err("cannot read it".to_owned()), |t| parse_rules(&t)) {
        Ok(r) => r,
        Err(e) => return (2, Vec::new(), vec![format!("{rules_path}: {e}")]),
    };
    let listed = match crate::gitutil::listed_files(root, false) {
        Ok(l) => l,
        Err(e) => return (2, Vec::new(), vec![e]),
    };
    let files: Vec<(String, String)> = listed
        .into_iter()
        .filter(|p| {
            Path::new(p)
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("py"))
        })
        .filter_map(|p| {
            let bytes = crate::gitutil::content_bytes(root, &p, staged)?;
            Some((p, String::from_utf8_lossy(&bytes).into_owned()))
        })
        .collect();
    let (mut code, mut good, mut bad) = (0, Vec::new(), Vec::new());
    for rule in &rules {
        let v = judge(rule, &files);
        if !v.problems.is_empty() {
            code = code.max(1);
            bad.extend(v.problems);
        } else if v.empty {
            let what = if rule.calls.is_empty() {
                format!("matches {}", rule.files.join(" "))
            } else {
                format!(
                    "in {} calls {}",
                    rule.files.join(" "),
                    rule.calls.join(" or ")
                )
            };
            bad.push(format!(
                "{}: no file {what}: nothing was checked -- the pattern moved",
                rule.name
            ));
            code = 2;
        } else {
            good.push(format!(
                "{}: {} file(s) bound, each calls {} ({} exempt)",
                rule.name,
                v.bound,
                rule.must_call.join(" or "),
                rule.exempt.len()
            ));
        }
    }
    (code, good, bad)
}

/// The structural step (opt-in: `GOH_REQUIRES_CALL` names the rules file).
#[must_use]
pub fn step(repo: &Path, cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let rules = cfg.requires_call.as_deref()?;
    let label = if staged {
        "a file that calls X calls Y (staged)"
    } else {
        "a file that calls X calls Y"
    };
    let start = crate::step_report::begin(label);
    match evaluate(repo, rules, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, bad) => {
            let text = bad.iter().fold(String::new(), |mut acc, l| {
                acc.push_str("✗ [requires_call] ");
                acc.push_str(l);
                acc.push('\n');
                acc
            });
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/requires_call.rs"),
                &text,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
#[path = "requires_call_tests.rs"]
mod tests;
