//! No process kill BY NAME -- Rust port of `checks/check_no_kill_by_name.py`
//! (Phase N1).
//!
//! A name is not an owner: zinc's harness ended every capture with
//! `pkill -9 -f "Camoufox.app"` and `SIGKILL`ed another session's browser in
//! the middle of a reply (2026-09-23). Kill what you started -- its pid, its
//! group (`pkill -g`), the tree below your pid (`pkill -P`). A `killall`
//! never is; a `pgrep`/`pidof` feeding a `kill` on the same line is the same
//! thing in two steps. Code and scripts only, comments stripped (Python:
//! comments and bare-string statements, as `ast` + `tokenize` see them).
//! Exemptions are a ratchet in `kill_by_name_allow.json`: one file, one exact
//! line (whitespace-insensitive), a reason; a stale entry fails.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::Path;

use regex::Regex;

const ALLOW_FILE: &str = "kill_by_name_allow.json";
const TAG: &str = "[no_kill_by_name]";
const STATUSES: [&str; 2] = ["legitimate", "unreviewed"];
const HASH_COMMENT: [&str; 9] = ["sh", "bash", "zsh", "py", "rb", "yml", "yaml", "mk", "pl"];
const SLASH_COMMENT: [&str; 18] = [
    "rs", "swift", "js", "mjs", "cjs", "ts", "tsx", "jsx", "go", "c", "cc", "cpp", "h", "hpp", "m",
    "mm", "java", "kt",
];
const NAMED: [&str; 5] = [
    "Makefile",
    "makefile",
    "GNUmakefile",
    "justfile",
    "Justfile",
];
const SCOPED_FLAGS: [&str; 6] = ["--parent", "--pgroup", "--session", "-P", "-g", "-s"];
// The command names, split so this file does not read as running them (the gate scans itself).
const PKILL: &str = concat!("p", "kill");
const KILLALL: &str = concat!("kill", "all");

/// `\w` or `-`: the edge of a command word.
fn edge(c: char) -> bool {
    c.is_alphanumeric() || c == '_' || c == '-'
}

/// Every `(?<![\w-])WORD(?![\w-])` occurrence of one of `words`, in order:
/// `(start, end, word)`.
fn words_in<'a>(code: &str, words: &[&'a str]) -> Vec<(usize, usize, &'a str)> {
    let mut out = Vec::new();
    let mut prev: Option<char> = None;
    let mut skip_to = 0usize;
    for (at, c) in code.char_indices() {
        if at >= skip_to && !prev.is_some_and(edge) {
            if let Some(w) = words.iter().find(|w| code[at..].starts_with(**w)) {
                let end = at + w.len();
                if !code[end..].chars().next().is_some_and(edge) {
                    out.push((at, end, *w));
                    skip_to = end;
                }
            }
        }
        prev = Some(c);
    }
    out
}

/// `SCOPED.search(text)`: an owner flag, quoted or bare, as its own word.
fn scoped(text: &str) -> bool {
    let mut prev: Option<char> = None;
    for (at, c) in text.char_indices() {
        if !prev.is_some_and(edge) {
            let rest = &text[at..];
            let rest = rest.strip_prefix(['"', '\'']).unwrap_or(rest);
            for flag in SCOPED_FLAGS {
                if let Some(after) = rest.strip_prefix(flag) {
                    if !after.chars().next().is_some_and(edge) {
                        return true;
                    }
                }
            }
        }
        prev = Some(c);
    }
    false
}

/// The rules that need a regex.
pub struct Rules {
    probe: Regex,
    hash_cut: Regex,
    slash_cut: Regex,
}

impl Rules {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("kill-by-name pattern {p:?}: {e}"));
        Ok(Self {
            probe: rx(r"(?:command\s+-[vV]|which|type(?:\s+-[a-zA-Z]+)?|hash)\s+$")?,
            hash_cut: rx(r"\s#")?,
            slash_cut: rx(r"\s//")?,
        })
    }

    /// Why this line kills by name, or None.
    #[must_use]
    pub fn finding(&self, code: &str) -> Option<String> {
        for (start, end, word) in words_in(code, &[PKILL, KILLALL]) {
            if self.probe.is_match(&code[..start]) {
                continue;
            }
            if word == PKILL && scoped(&code[end..]) {
                continue;
            }
            return Some(format!("{word} matches by name"));
        }
        let (_, end, word) = words_in(code, &["pgrep", "pidof"]).into_iter().next()?;
        (!words_in(code, &["kill"]).is_empty() && !scoped(&code[end..]))
            .then(|| format!("a {word} lookup feeds a kill"))
    }

    fn plain_code_lines(&self, text: &str, ext: &str) -> BTreeMap<usize, String> {
        let cut = if SLASH_COMMENT.contains(&ext) {
            &self.slash_cut
        } else {
            &self.hash_cut
        };
        let mut out = BTreeMap::new();
        for (i, line) in text.split('\n').enumerate() {
            let s = line.trim();
            if ["#", "//", "/*", "*", "--"]
                .iter()
                .any(|p| s.starts_with(p))
                && !s.starts_with("#!")
            {
                continue;
            }
            let code = cut.find(line).map_or(line, |m| &line[..m.start()]);
            out.insert(i + 1, code.to_owned());
        }
        out
    }

    fn code_lines(&self, rel: &str, text: &str) -> BTreeMap<usize, String> {
        let ext = ext_of(rel);
        if ext == "py" {
            if let Some(lines) = python_code_lines(text) {
                return lines;
            }
        }
        self.plain_code_lines(text, ext)
    }
}

/// `{lineno: code}` with comments cut at their column and every statement
/// that is only a `str` literal dropped -- `ast.walk` + `tokenize`'s view. A
/// file the tokenizer refuses is `None`, and the plain rules read it.
fn python_code_lines(text: &str) -> Option<BTreeMap<usize, String>> {
    let lexed = crate::pylex::lex(text)?;
    let mut lines: Vec<String> = text.split('\n').map(str::to_owned).collect();
    for (row, col) in &lexed.comments {
        if let Some(line) = lines.get_mut(row - 1) {
            *line = line.chars().take(*col).collect();
        }
    }
    let prose = lexed.bare_strings();
    Some(
        lines
            .into_iter()
            .enumerate()
            .map(|(i, l)| (i + 1, l))
            .filter(|(n, _)| !prose.iter().any(|&(a, b)| a <= *n && *n <= b))
            .collect(),
    )
}

fn basename(rel: &str) -> &str {
    rel.rsplit('/').next().unwrap_or(rel)
}

/// `os.path.splitext(rel)[1]` without its dot.
fn ext_of(rel: &str) -> &str {
    let name = basename(rel);
    match name.rfind('.') {
        Some(dot) if name[..dot].chars().any(|c| c != '.') => &name[dot + 1..],
        _ => "",
    }
}

fn in_scope(rel: &str, blob: &[u8]) -> bool {
    let name = basename(rel);
    let ext = ext_of(rel);
    if NAMED.contains(&name) || HASH_COMMENT.contains(&ext) || SLASH_COMMENT.contains(&ext) {
        return true;
    }
    ext.is_empty() && blob.starts_with(b"#!")
}

/// One hit: (path, line, stripped code, why).
pub type Hit = (String, usize, String, String);

fn scan(rules: &Rules, root: &Path, files: &[String], staged: bool) -> (Vec<Hit>, usize) {
    let mut hits = Vec::new();
    let mut checked = 0usize;
    for rel in files {
        if basename(rel) == ALLOW_FILE {
            continue;
        }
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        if blob[..blob.len().min(4096)].contains(&0) || !in_scope(rel, &blob) {
            continue;
        }
        checked += 1;
        let text = String::from_utf8_lossy(&blob);
        for (lineno, code) in rules.code_lines(rel, &text) {
            if let Some(why) = rules.finding(&code) {
                hits.push((rel.clone(), lineno, code.trim().to_owned(), why));
            }
        }
    }
    (hits, checked)
}

/// One allowlist entry, as read.
pub struct Entry {
    pub path: String,
    pub line: String,
    pub unreviewed: bool,
}

fn nonempty(v: Option<&serde_json::Value>) -> Option<String> {
    v.and_then(serde_json::Value::as_str)
        .filter(|s| !s.trim().is_empty())
        .map(str::to_owned)
}

fn load_allow(root: &Path, staged: bool) -> (Vec<Entry>, Vec<String>) {
    let Some(blob) = crate::gitutil::content_bytes(root, ALLOW_FILE, staged) else {
        return (Vec::new(), Vec::new());
    };
    let data: serde_json::Value = match std::str::from_utf8(&blob)
        .map_err(|e| e.to_string())
        .and_then(|t| serde_json::from_str(t).map_err(|e| e.to_string()))
    {
        Ok(v) => v,
        Err(e) => {
            return (
                Vec::new(),
                vec![format!("{ALLOW_FILE} is not valid JSON: {e}")],
            )
        }
    };
    let Some(items) = data.get("entries").and_then(serde_json::Value::as_array) else {
        return (
            Vec::new(),
            vec![format!("{ALLOW_FILE} must be {{\"entries\": [...]}}")],
        );
    };
    let mut entries = Vec::new();
    let mut problems = Vec::new();
    for (i, e) in items.iter().enumerate() {
        let fields = (
            nonempty(e.get("path")),
            nonempty(e.get("line")),
            nonempty(e.get("reason")),
        );
        let (Some(path), Some(line), Some(_)) = fields else {
            problems.push(format!(
                "{ALLOW_FILE} entry {i} needs a non-empty path, line and reason"
            ));
            entries.push(Entry {
                path: String::new(),
                line: String::new(),
                unreviewed: true,
            });
            continue;
        };
        let status = e.get("status");
        let unreviewed = status.is_none_or(|s| s.as_str() == Some("unreviewed"));
        if !status.is_none_or(|s| s.as_str().is_some_and(|s| STATUSES.contains(&s))) {
            problems.push(format!(
                "{ALLOW_FILE} entry {i}: status must be one of {}",
                STATUSES.join(", ")
            ));
        }
        entries.push(Entry {
            path,
            line,
            unreviewed,
        });
    }
    (entries, problems)
}

fn key(line: &str) -> String {
    line.split_whitespace().collect()
}

/// The reference's report: (exit code, stdout).
fn report(
    staged: bool,
    hits: &[Hit],
    checked: usize,
    entries: &[Entry],
    scanned: &BTreeSet<&str>,
) -> (i32, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut used: BTreeSet<usize> = BTreeSet::new();
    let mut left: Vec<&Hit> = Vec::new();
    for hit in hits {
        let k = key(&hit.2);
        match entries
            .iter()
            .position(|e| e.path == hit.0 && key(&e.line) == k)
        {
            Some(i) => {
                used.insert(i);
            }
            None => left.push(hit),
        }
    }
    let stale: Vec<&Entry> = entries
        .iter()
        .enumerate()
        .filter(|(i, e)| !used.contains(i) && scanned.contains(e.path.as_str()))
        .map(|(_, e)| e)
        .collect();
    let mut s = String::new();
    if !left.is_empty() {
        let _ = writeln!(
            s,
            "✗ {TAG} {} process kill(s) by name in {scope} code:",
            left.len()
        );
        for (rel, lineno, code, why) in left.iter().take(40) {
            let shown: String = code.chars().take(140).collect();
            let _ = writeln!(s, "    {rel}:{lineno}: {shown}  ({why})");
        }
        s.push_str(
            "    kill what you started: its pid, its process group (pkill -g, killpg), or the\n",
        );
        s.push_str(
            "    tree below your own pid (pkill -P). A name matches processes you do not own.\n",
        );
        let _ = writeln!(
            s,
            "    A kill that must stand goes in {ALLOW_FILE} with a reason."
        );
    }
    if !stale.is_empty() {
        let _ = writeln!(
            s,
            "✗ {TAG} {} stale entr(ies) in {ALLOW_FILE}: the kill is gone, the exemption stayed:",
            stale.len()
        );
        for e in &stale {
            let _ = writeln!(s, "    {}: {}", e.path, e.line);
        }
    }
    if !left.is_empty() || !stale.is_empty() {
        return (1, s);
    }
    if checked == 0 {
        if staged {
            let _ = writeln!(s, "✓ {TAG} nothing staged — 0 code files to check");
            return (0, s);
        }
        let _ = writeln!(
            s,
            "✗ {TAG} nothing to check (tracked) — refusing to report clean over zero code files"
        );
        return (1, s);
    }
    let note = if hits.is_empty() {
        String::new()
    } else {
        format!(", {} allowlisted in {ALLOW_FILE}", hits.len())
    };
    let _ = writeln!(
        s,
        "✓ {TAG} OK — {checked} {scope} code files, no kill by name{note}"
    );
    let debt = used.iter().filter(|i| entries[**i].unreviewed).count();
    if debt > 0 {
        let _ = writeln!(
            s,
            "⚠ {TAG}   {debt} of them 'unreviewed': found when the gate was seeded, not yet decided"
        );
    }
    (0, s)
}

fn run(staged: bool, exclude: &str) -> (i32, String) {
    let skip = match crate::steps::compile_exclude(exclude) {
        Ok(f) => f,
        Err(m) => return (2, format!("✗ {TAG} {m}\n")),
    };
    let rules = match Rules::new() {
        Ok(r) => r,
        Err(m) => return (2, format!("✗ {TAG} {m}\n")),
    };
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        return (2, format!("✗ {TAG} not a git repository\n"));
    };
    let files: Vec<String> = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f
            .into_iter()
            .filter(|f| !skip.as_ref().is_some_and(|x| x.is_match(f)))
            .collect(),
        Err(m) => return (2, format!("✗ {TAG} {m}\n")),
    };
    let (entries, problems) = load_allow(&root, staged);
    if !problems.is_empty() {
        let mut s = String::new();
        for p in problems {
            let _ = writeln!(s, "✗ {TAG} {p}");
        }
        return (1, s);
    }
    let (hits, checked) = scan(&rules, &root, &files, staged);
    let scanned: BTreeSet<&str> = files.iter().map(String::as_str).collect();
    report(staged, &hits, checked, &entries, &scanned)
}

/// `goh kill-by-name [--staged] [--exclude RE]`, or `--code-lines PATH` to
/// print the code lines this check reads from stdin, as JSON (the parity
/// harness's view of the comment and docstring stripping).
#[must_use]
pub fn run_command(staged: bool, exclude: &str, code_lines: Option<&str>) -> i32 {
    if let Some(rel) = code_lines {
        let mut text = String::new();
        if std::io::Read::read_to_string(&mut std::io::stdin(), &mut text).is_err() {
            eprintln!("✗ {TAG} stdin is not UTF-8 text");
            return 2;
        }
        let rules = match Rules::new() {
            Ok(r) => r,
            Err(m) => {
                eprintln!("✗ {TAG} {m}");
                return 2;
            }
        };
        let map: serde_json::Map<String, serde_json::Value> = rules
            .code_lines(rel, &text)
            .into_iter()
            .map(|(n, l)| (n.to_string(), serde_json::Value::String(l)))
            .collect();
        println!("{}", serde_json::Value::Object(map));
        return 0;
    }
    let (code, out) = run(staged, exclude);
    print!("{out}");
    code
}

/// The structural step (opt-in, `GOH_NO_KILL_BY_NAME`); the delegated step's label.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    if !crate::gatesrc::opt_in(cfg, "GOH_NO_KILL_BY_NAME") {
        return None;
    }
    let label = if staged {
        "no process kill by name (staged)"
    } else {
        "no process kill by name"
    };
    let start = crate::step_report::begin(label);
    match run(staged, &cfg.exclude) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, out) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported(
                    "crates/goh/src/killname/mod.rs",
                    "check_no_kill_by_name.py",
                ),
                &out,
                start,
            );
            Some(code)
        }
    }
}
