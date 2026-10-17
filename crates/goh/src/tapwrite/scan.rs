//! Which lines of a script or workflow write a Homebrew tap directly, read line by line.
//!
//! Two shapes, both seen in the estate (`tests.rs` carries the originals):
//!
//!   * an API write -- `gh api` with a non-GET method, or with fields and no method (which `gh`
//!     sends as a POST), or `curl` with a write method or a body, to
//!     `repos/<owner>/homebrew-<name>/contents/...`;
//!   * a push from a clone of the tap -- a `git clone` / `gh repo clone` of a `homebrew-*` repo,
//!     then a `git push` later in the same file.
//!
//! The repo usually arrives through a variable (`TAP="ztomer/homebrew-tap"`, then
//! `repos/${TAP}/contents`), so every simple assignment in the file whose value names a
//! `homebrew-*` repo is substituted into each line before it is judged: shell `$X`, `${X}` and
//! `${X:-...}`, and a Python f-string's `{X}`. A backslash-continued shell command is one line,
//! reported at its first. A comment line is never a write, and the one escape is
//! `# tap-write-ok: <reason>` on the line or in the comment lines directly above it.

use regex::Regex;
use std::collections::BTreeMap;

/// The per-line exemption: `# tap-write-ok: <reason>`.
pub const MARKER: &str = "tap-write-ok:";

/// Script and workflow suffixes this check reads.
const SUFFIXES: [&str; 8] = ["sh", "bash", "zsh", "ksh", "py", "yml", "yaml", "mk"];
/// Build files read by name.
const NAMES: [&str; 4] = ["Makefile", "GNUmakefile", "Justfile", "justfile"];

/// Whether `rel` is read, judged by its name alone.
///
/// `Some(true)` a script or workflow, `Some(false)` never (a suffix this check does not read),
/// `None` an extensionless file whose first bytes decide (a shebang script is read).
#[must_use]
pub fn by_name(rel: &str) -> Option<bool> {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    if NAMES.contains(&name) {
        return Some(true);
    }
    std::path::Path::new(name)
        .extension()
        .and_then(|e| e.to_str())
        .map(|ext| SUFFIXES.iter().any(|s| ext.eq_ignore_ascii_case(s)))
}

/// Whether `rel`, whose content starts with `head`, is read.
#[must_use]
pub fn in_scope(rel: &str, head: &[u8]) -> bool {
    by_name(rel).unwrap_or_else(|| head.starts_with(b"#!"))
}

/// What one finding is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Problem {
    /// A write to a tap's contents API.
    ApiWrite,
    /// A push from a clone of the tap made on `clone_line`.
    PushFromClone { clone_line: usize },
    /// The marker, with no reason after it.
    EmptyReason,
    /// The marker, with no write under it: it exempts nothing.
    StaleMarker,
}

/// One finding: the 1-based line and what is wrong there.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub problem: Problem,
}

/// The patterns a scan reads with, compiled once per run ([`Scanner::compile`]).
pub struct Scanner {
    assign: Regex,
    tap_name: Regex,
    tap_contents: Regex,
    gh_api: Regex,
    curl: Regex,
    method: Regex,
    gh_fields: Regex,
    curl_body: Regex,
    clone: Regex,
    push: Regex,
}

impl Scanner {
    /// Compile every built-in pattern.
    ///
    /// # Errors
    ///
    /// A message naming the pattern, only if a built-in pattern fails to compile -- a
    /// programming error, never input-dependent.
    pub fn compile() -> Result<Self, String> {
        let re = |name: &str, pat: &str| {
            Regex::new(pat).map_err(|e| format!("built-in {name} pattern failed: {e}"))
        };
        Ok(Self {
            assign: re(
                "assignment",
                r"^\s*(?:export\s+|readonly\s+|local\s+|declare\s+(?:-\w+\s+)?)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]\s*(.*)$",
            )?,
            tap_name: re("tap name", r"homebrew-[A-Za-z0-9._-]")?,
            tap_contents: re(
                "tap contents",
                r#"repos/[^\s"',]*?homebrew-[A-Za-z0-9._-]+\}?/contents\b"#,
            )?,
            gh_api: re("gh api", r#"\bgh["']?(?:\s*,\s*["']|\s+)api\b"#)?,
            curl: re("curl", r"\bcurl\b")?,
            method: re(
                "method",
                r#"(?:^|[\s"'\[,])(?:-X|--method|--request)(?:\s*=\s*|\s+|["']\s*,\s*["']|)["']?([A-Za-z]+)"#,
            )?,
            gh_fields: re(
                "gh fields",
                r#"(?:^|[\s"'])(?:-f|-F|--field|--raw-field|--input)(?:[\s=]|["'])"#,
            )?,
            curl_body: re(
                "curl body",
                r"(?:^|\s)(?:-d|--data|--data-binary|--data-raw|-T|--upload-file)(?:[\s=])",
            )?,
            clone: re(
                "clone",
                r#"\bgit\b[^|;&]*\bclone\b|\bgh\s+repo\s+clone\b|["']git["'][^\]]*["']clone["']"#,
            )?,
            push: re(
                "push",
                r#"\bgit\b[^|;&]*\bpush\b|["']git["'][^\]]*["']push["']"#,
            )?,
        })
    }
}

/// One command as the shell reads it: physical lines joined across trailing backslashes.
struct Logical {
    start: usize,
    end: usize,
    text: String,
}

fn is_comment(line: &str) -> bool {
    line.trim_start().starts_with('#')
}

fn logical_lines(lines: &[&str]) -> Vec<Logical> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        let start = i;
        let mut text = String::new();
        loop {
            let line = lines[i].trim_end();
            if let Some(cont) = line.strip_suffix('\\') {
                text.push_str(cont);
                text.push(' ');
                if i + 1 < lines.len() {
                    i += 1;
                    continue;
                }
            } else {
                text.push_str(line);
            }
            break;
        }
        out.push(Logical {
            start: start + 1,
            end: i + 1,
            text,
        });
        i += 1;
    }
    out
}

fn unquote(value: &str) -> &str {
    let v = value.trim();
    for q in ['"', '\''] {
        if let Some(inner) = v.strip_prefix(q).and_then(|s| s.strip_suffix(q)) {
            return inner;
        }
    }
    v
}

/// Every simple assignment whose value names a `homebrew-*` repo, as a substitution.
fn tap_vars(sc: &Scanner, lines: &[&str]) -> Vec<(Regex, String)> {
    let mut vars: BTreeMap<String, String> = BTreeMap::new();
    for line in lines.iter().filter(|l| !is_comment(l)) {
        if let Some(c) = sc.assign.captures(line) {
            let value = unquote(&c[2]);
            if sc.tap_name.is_match(value) {
                vars.insert(c[1].to_owned(), value.to_owned());
            }
        }
    }
    vars.into_iter()
        .filter_map(|(name, value)| {
            let n = regex::escape(&name);
            let re = format!(r"\$\{{{n}(?:[:#%/^,][^}}]*)?\}}|\${n}\b|\{{{n}\}}");
            Regex::new(&re).ok().map(|r| (r, value))
        })
        .collect()
}

fn substitute(text: &str, vars: &[(Regex, String)]) -> String {
    let mut out = text.to_owned();
    for (re, value) in vars {
        out = re.replace_all(&out, regex::NoExpand(value)).into_owned();
    }
    out
}

fn writes_method(sc: &Scanner, text: &str) -> Option<bool> {
    sc.method
        .captures(text)
        .map(|c| !c[1].eq_ignore_ascii_case("GET"))
}

fn is_api_write(sc: &Scanner, text: &str) -> bool {
    if !sc.tap_contents.is_match(text) {
        return false;
    }
    if sc.gh_api.is_match(text) {
        return writes_method(sc, text).unwrap_or_else(|| sc.gh_fields.is_match(text));
    }
    sc.curl.is_match(text)
        && text.contains("api.github.com/")
        && writes_method(sc, text).unwrap_or_else(|| sc.curl_body.is_match(text))
}

/// The marker on `line`, if any: its reason (possibly empty).
fn marker_on(line: &str) -> Option<&str> {
    let at = line.find(MARKER)?;
    line[..at]
        .contains('#')
        .then(|| line[at + MARKER.len()..].trim())
}

/// The marker covering the command on lines `start..=end` (1-based): inline, or in the comment
/// lines directly above `start`. Returns its line and reason.
fn covering_marker<'a>(lines: &[&'a str], start: usize, end: usize) -> Option<(usize, &'a str)> {
    for n in start..=end {
        if let Some(reason) = marker_on(lines[n - 1]) {
            return Some((n, reason));
        }
    }
    let mut n = start - 1;
    while n >= 1 && is_comment(lines[n - 1]) {
        if let Some(reason) = marker_on(lines[n - 1]) {
            return Some((n, reason));
        }
        n -= 1;
    }
    None
}

/// Every finding in `src`, ordered by line.
#[must_use]
pub fn findings(sc: &Scanner, src: &str) -> Vec<Finding> {
    let lines: Vec<&str> = src.lines().collect();
    let vars = tap_vars(sc, &lines);
    let mut raw: Vec<(usize, usize, Problem)> = Vec::new();
    let mut clone_at: Option<usize> = None;
    for cmd in logical_lines(&lines) {
        if is_comment(lines[cmd.start - 1]) {
            continue;
        }
        let text = substitute(&cmd.text, &vars);
        if is_api_write(sc, &text) {
            raw.push((cmd.start, cmd.end, Problem::ApiWrite));
        }
        if let Some(clone_line) = clone_at {
            if sc.push.is_match(&text) {
                raw.push((cmd.start, cmd.end, Problem::PushFromClone { clone_line }));
            }
        } else if sc.clone.is_match(&text) && sc.tap_name.is_match(&text) {
            clone_at = Some(cmd.start);
        }
    }
    let mut used = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for (start, end, problem) in raw {
        match covering_marker(&lines, start, end) {
            Some((at, reason)) => {
                used.insert(at);
                if reason.is_empty() {
                    out.push(Finding {
                        line: start,
                        problem: Problem::EmptyReason,
                    });
                }
            }
            None => out.push(Finding {
                line: start,
                problem,
            }),
        }
    }
    for (i, line) in lines.iter().enumerate() {
        if marker_on(line).is_some() && !used.contains(&(i + 1)) {
            out.push(Finding {
                line: i + 1,
                problem: Problem::StaleMarker,
            });
        }
    }
    out.sort_by_key(|f| f.line);
    out
}
