//! No emptiness assertion in the shapes clippy refuses -- Rust port of
//! the retired `checks/check_no_empty_assert.py` (Phase N1).
//!
//! `assert!(v.is_empty())`, `assert!(v.len() == 0)` and kin are what clippy's
//! `assert_is_empty` / `len_zero` refuse -- and clippy is SILENT on the same
//! shapes with a message (`assert!(v.is_empty(), "msg")`), measured against
//! clippy 1.99.0. The condition must be the WHOLE condition (a compound or a
//! nested test is not this shape), and a message that interpolates the value
//! is already the improvement asked for. The measured table is the crate's
//! own test below.

use regex::Regex;

use crate::noallow::Scanner as NoAllow;

/// The compiled shapes.
pub struct Rules {
    assert_macro: Regex,
    is_empty: Regex,
    len_zero: Regex,
    string_literal: Regex,
    quotes: Regex,
}

impl Rules {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("empty-assert pattern {p:?}: {e}"));
        Ok(Self {
            assert_macro: rx(r"^(?:debug_)?assert!\s*\(")?,
            is_empty: rx(r"^!?\s*.+\.is_empty\s*\(\s*\)$")?,
            len_zero: rx(
                r"^(?:!?\s*.+\.len\s*\(\s*\)\s*(?:==|!=|<|>)\s*0\b|!?\s*0\s*(?:==|!=|<|>)\s*.+\.len\s*\(\s*\))$",
            )?,
            string_literal: rx(r#""(?:[^"\\]|\\.)*""#)?,
            quotes: rx(r#""+"#)?,
        })
    }

    /// `(lineno, invocation)` for each emptiness assertion in one file.
    #[must_use]
    pub fn findings(&self, text: &str) -> Vec<(usize, String)> {
        let mut out = Vec::new();
        let (mut depth, mut start) = (0usize, 0usize);
        let mut buf: Vec<char> = Vec::new();
        let mut comment = 0usize;
        for (n, line) in text.split('\n').enumerate() {
            let (code, next) = NoAllow::strip_line(line, comment);
            comment = next;
            let free: Vec<char> = self
                .string_literal
                .replace_all(&code, |c: &regex::Captures<'_>| {
                    c[0].chars()
                        .map(|ch| if ch == '{' || ch == '}' { ch } else { '"' })
                        .collect::<String>()
                })
                .chars()
                .collect();
            let mut i = 0usize;
            while i < free.len() {
                if depth == 0 {
                    let prev_ok = i == 0
                        || !(free[i - 1].is_alphanumeric()
                            || free[i - 1] == '_'
                            || free[i - 1] == ':');
                    let rest: String = free[i..].iter().collect();
                    match self.assert_macro.find(&rest).filter(|_| prev_ok) {
                        Some(m) => {
                            start = n + 1;
                            depth = 1;
                            buf = m.as_str().chars().collect();
                            i += m.as_str().chars().count();
                        }
                        None => i += 1,
                    }
                    continue;
                }
                let ch = free[i];
                buf.push(ch);
                if ch == '(' {
                    depth += 1;
                } else if ch == ')' {
                    depth -= 1;
                    if depth == 0 {
                        let joined: String = buf.iter().collect();
                        if self.is_violation(&joined) {
                            out.push((start, joined));
                        }
                        buf.clear();
                    }
                }
                i += 1;
            }
            if depth > 0 {
                buf.push(' ');
            }
        }
        out
    }

    fn is_violation(&self, invocation: &str) -> bool {
        let cond = condition_of(invocation);
        if cond.contains("&&") || cond.contains("||") || !format_args(invocation).is_empty() {
            return false;
        }
        self.is_empty.is_match(&cond) || self.len_zero.is_match(&cond)
    }

    /// The invocation as evidence: each run of mask quotes back to `""`, whitespace collapsed.
    #[must_use]
    pub fn readable(&self, invocation: &str) -> String {
        self.quotes
            .replace_all(invocation, "\"\"")
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
    }
}

/// The macro's first argument: up to the first top-level comma.
fn condition_of(invocation: &str) -> String {
    let c: Vec<char> = invocation.chars().collect();
    let open = c.iter().position(|ch| *ch == '(').unwrap_or(0);
    let mut depth = 0isize;
    for (i, ch) in c.iter().enumerate() {
        match ch {
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 1 => {
                return c[open + 1..i].iter().collect::<String>().trim().to_owned()
            }
            _ => {}
        }
    }
    let body: String = c[(open + 1).min(c.len())..]
        .iter()
        .collect::<String>()
        .trim()
        .to_owned();
    body.strip_suffix(')')
        .map_or_else(|| body.clone(), |b| b.trim().to_owned())
}

/// The format arguments after the condition that interpolate something.
fn format_args(invocation: &str) -> Vec<String> {
    let c: Vec<char> = invocation.chars().collect();
    let mut depth = 0isize;
    let mut start = None;
    for (i, ch) in c.iter().enumerate() {
        match ch {
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 1 => {
                start = Some(i + 1);
                break;
            }
            _ => {}
        }
    }
    let Some(start) = start else {
        return Vec::new();
    };
    let rest: String = c[start..].iter().collect();
    let trailing = rest.trim_end();
    let trailing = trailing.strip_suffix(')').unwrap_or(trailing);
    trailing
        .split(',')
        .filter(|a| a.contains('{') && a.contains('}'))
        .map(str::to_owned)
        .collect()
}

pub(crate) fn run(staged: bool, exclude: &str) -> (i32, String, String) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, String::new(), format!("✗ [no_empty_assert] {m}\n")),
    };
    let rules = match Rules::new() {
        Ok(r) => r,
        Err(m) => return (2, String::new(), format!("✗ [no_empty_assert] {m}\n")),
    };
    let root = crate::gitutil::repo_root()
        .map_or_else(|| std::path::PathBuf::from("."), std::path::PathBuf::from);
    let listed = match crate::gitutil::listed_files(&root, staged) {
        Ok(l) => l,
        Err(m) => return (2, String::new(), format!("✗ [no_empty_assert] {m}\n")),
    };
    let files: Vec<&String> = listed
        .iter()
        .filter(|f| {
            f.as_bytes().ends_with(b".rs")
                && crate::noallow::is_compiled_src(f)
                && !exclude.as_ref().is_some_and(|x| x.is_match(f))
        })
        .collect();
    let mut hits = Vec::new();
    for rel in &files {
        let Some(blob) = crate::gitutil::content_bytes(&root, rel, staged) else {
            continue;
        };
        let text = String::from_utf8_lossy(&blob);
        if crate::noallow::is_generated(&text) {
            continue;
        }
        for (n, inv) in rules.findings(&text) {
            hits.push(format!("{rel}:{n}: {}", rules.readable(&inv)));
        }
    }
    if !hits.is_empty() {
        let scope = if staged { "staged" } else { "tracked" };
        let mut out = format!(
            "✗ [no_empty_assert] {} emptiness assert(s) in {scope} Rust source:\n",
            hits.len()
        );
        for h in hits.iter().take(40) {
            out.push_str("    ");
            out.push_str(h);
            out.push('\n');
        }
        out.push_str(
            "write `assert_eq!(x.len(), 0)` (or `assert_ne!(x.len(), 0)` for the\nnegated case) — those are clippy's own suggestions and are not flagged.\nTo fix a whole tree at once: cargo clippy --fix --all-targets -- -A clippy::pedantic\n",
        );
        return (1, out, String::new());
    }
    if files.is_empty() && crate::noallow::has_own_rust(&listed, exclude.as_ref(), staged) {
        return (
            1,
            String::new(),
            "✗ [no_empty_assert] this repo has Rust (a Cargo.toml is tracked) and the scan matched NO compiled source. That is not a clean run -- the crate layout moved out from under src/, benches/ and build.rs.\n".to_owned(),
        );
    }
    (
        0,
        format!("✓ [no_empty_assert] OK — {} files clean\n", files.len()),
        String::new(),
    )
}

/// `goh empty-assert [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let (code, out, err) = run(staged, exclude);
    print!("{out}");
    eprint!("{err}");
    code
}

#[cfg(test)]
mod tests {
    use super::Rules;

    /// The measured table: one shape per line against clippy 1.99.0
    /// (2026-10-02). `false` rows matter as much: a checker that flagged
    /// `assert_eq!(v.len(), 0)` would be flagging the fix.
    const TABLE: &[(&str, bool)] = &[
        ("assert!(v.is_empty());", true),
        ("assert!(!v.is_empty());", true),
        ("assert!(v.len() == 0);", true),
        ("assert!(0 == v.len());", true),
        ("assert!(v.len() > 0);", true),
        ("debug_assert!(v.is_empty());", true),
        ("assert!(v.is_empty(), \"with a message\");", true),
        ("assert!(!v.is_empty(), \"msg\");", true),
        ("assert!(s\n    .as_bytes()\n    .is_empty());", true),
        (
            "assert!(s.as_bytes().is_empty(),\n    \"split message\");",
            true,
        ),
        (
            "assert!(Style::load_zstyle(&dir.join(\"missing\")).is_empty());",
            true,
        ),
        ("assert!(load(\"a\").is_empty(), \"no rows\");", true),
        ("assert!(v.ok()?.is_empty());", true),
        ("assert!(take!(&d).is_empty());", true),
        ("assert_eq!(v.len(), 0);", false),
        ("assert_eq!(v.to_vec(), Vec::<u8>::new());", false),
        ("assert_ne!(v.len(), 0);", false),
        ("let x = v.is_empty(); assert!(x);", false),
        ("assert!(v.iter().next().is_none());", false),
        ("assert!(v.is_empty() && ok);", false),
        ("assert!(ok && v.is_empty());", false),
        ("assert!(!v.is_empty() || ok);", false),
        ("assert!(v.is_empty() == true);", false),
        ("assert!(v.first().is_some_and(|x| !x.is_empty()));", false),
        ("assert!(v.is_empty() && w.is_empty());", false),
        ("assert!(matches!(v.len(), 0));", false),
        ("my_assert!(v.is_empty());", false),
        ("a::assert!(v.is_empty());", false),
        ("assert_eq!(v.len(), 0usize);", false),
        ("// assert!(v.is_empty()); is prose\nfn f() {}\n", false),
        ("/* assert!(v.is_empty()); */\nfn f() {}\n", false),
        (
            "const S: &str = \"assert!(v.is_empty());\";\nfn f() {}\n",
            false,
        ),
        ("assert!(v.is_empty(), \"left {v:?} over\");", false),
    ];

    #[test]
    fn every_row_of_the_measured_table_holds() {
        let rules = Rules::new().unwrap_or_else(|e| unreachable!("{e}"));
        for (source, want) in TABLE {
            assert_eq!(!rules.findings(source).is_empty(), *want, "{source:?}");
        }
    }
}
