//! Prose -> the MARKED claims it makes. Port of the retired `checks/_claim_text.py`.
//!
//! Two spellings and no others: the BACKTICK form (number and target both in
//! backticks) and the MARKER form (`claim:` first on the line, after at most
//! a bullet or a comment opener). Fenced blocks are examples, not claims, and
//! so is a non-docstring string literal in Python (data the program
//! supplies -- where a checker's own fixtures live).

use regex::{Captures, Regex};

/// One marked claim.
#[derive(Clone, Debug)]
pub struct Claim {
    pub number: u64,
    pub kind: &'static str,
    pub glob: Option<String>,
    pub target: String,
    pub name: String,
    pub line: usize,
    pub text: String,
    pub malformed: Option<String>,
}

pub(crate) const UNITS: [(&str, &str); 12] = [
    ("line", "lines"),
    ("lines", "lines"),
    ("test", "tests"),
    ("tests", "tests"),
    ("gate", "declared"),
    ("gates", "declared"),
    ("step", "declared"),
    ("steps", "declared"),
    ("check", "declared"),
    ("checks", "declared"),
    ("file", "files"),
    ("files", "files"),
];

const MARKUP: [&str; 3] = [".md", ".markdown", ".rst"];
const TEXT_SUFFIXES: [&str; 26] = [
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".rs",
    ".swift",
    ".kt",
    ".go",
    ".c",
    ".h",
    ".cpp",
    ".m",
    ".mm",
    ".js",
    ".ts",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".gatesrc",
];

fn unit_kind(word: &str) -> &'static str {
    UNITS
        .iter()
        .find(|(w, _)| *w == word)
        .map_or("", |(_, k)| k)
}

/// The two claim forms, compiled.
pub struct Grammar {
    backtick: Regex,
    marker: Regex,
    fences: crate::mdtext::Fences,
}

/// `os.path.splitext(rel)[1].lower()`.
#[must_use]
pub fn suffix(rel: &str) -> String {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    match name.rfind('.') {
        Some(dot) if name[..dot].chars().any(|c| c != '.') => name[dot..].to_lowercase(),
        _ => String::new(),
    }
}

/// Is this repo-relative path a file a claim may live in?
#[must_use]
pub fn scannable(rel: &str) -> bool {
    rel.rsplit('/').next() == Some(".gatesrc") || TEXT_SUFFIXES.contains(&suffix(rel).as_str())
}

impl Grammar {
    /// # Errors
    /// A built-in pattern that does not compile.
    pub fn new() -> Result<Self, String> {
        // `sorted(UNITS, key=len, reverse=True)`: longest first, stable.
        let mut words: Vec<&str> = UNITS.iter().map(|(w, _)| *w).collect();
        words.sort_by_key(|w| std::cmp::Reverse(w.len()));
        let units = words.join("|");
        let target = r"[A-Za-z0-9_][A-Za-z0-9_./+-]*(?::[A-Za-z_][A-Za-z0-9_]*)?";
        let glob = r"\*\.[A-Za-z0-9_+*?-]+";
        let middle = format!(r"(?:(?P<glob>{glob})\s+)?(?P<unit>{units})\s+(?:in|under)\s+");
        let comment = r"(?:<!--|#+|//|--|;|%)[ \t]*";
        let prefix = format!(r"[ \t]*(?:[-*>+][ \t]+)?(?:{comment})?");
        let rx = |p: &str| Regex::new(p).map_err(|e| format!("claim pattern: {e}"));
        Ok(Self {
            backtick: rx(&format!(r"`(?P<num>\d+)`\s+{middle}`(?P<target>{target})`"))?,
            marker: rx(&format!(
                r"^{prefix}claim:[ \t]*`?(?P<num>\d+)`?[ \t]+{middle}`?(?P<target>{target})`?"
            ))?,
            fences: crate::mdtext::Fences::new()?,
        })
    }

    fn build(c: &Captures<'_>, line: usize, text: &str, promised: bool) -> Option<Claim> {
        let kind = unit_kind(c.name("unit")?.as_str());
        let glob = c.name("glob").map(|m| m.as_str().to_owned());
        let mut malformed = None;
        if glob.is_some() && kind != "files" {
            if !promised {
                return None;
            }
            malformed = Some(format!(
                "an extension applies to a `files` claim only, not to a `{kind}` one"
            ));
        } else if kind == "files" && glob.is_none() {
            if !promised {
                return None;
            }
            malformed = Some(
                "a `files` claim must name the extension it counts (`*.py`), or it is not derivable"
                    .to_owned(),
            );
        }
        let raw = c.name("target")?.as_str();
        let (target, name) = raw.rfind(':').map_or_else(
            || (raw.to_owned(), String::new()),
            |i| (raw[..i].to_owned(), raw[i + 1..].to_owned()),
        );
        Some(Claim {
            // Digits only, so a parse failure is an overflow: no claim.
            number: c.name("num")?.as_str().parse().ok()?,
            kind,
            glob,
            target,
            name,
            line,
            text: text.to_owned(),
            malformed,
        })
    }

    /// Every claim one line makes: the backtick form first, then `claim:`.
    #[must_use]
    pub fn claims_in_line(&self, line: &str, lineno: usize) -> Vec<Claim> {
        if let Some(c) = self.backtick.captures(line) {
            if let Some(claim) = Self::build(&c, lineno, line, false) {
                return vec![claim];
            }
        }
        self.marker
            .captures(line)
            .and_then(|c| Self::build(&c, lineno, line, true))
            .into_iter()
            .collect()
    }

    /// Every claim in one file, in line order.
    #[must_use]
    pub fn claims_in_file(&self, rel: &str, text: &str) -> Vec<Claim> {
        let sfx = suffix(rel);
        let mut body = self.fences.strip(text, MARKUP.contains(&sfx.as_str()));
        if sfx == ".py" {
            body = python_prose(&body);
        }
        body.split('\n')
            .enumerate()
            .filter(|(_, l)| !l.is_empty())
            .flat_map(|(i, l)| self.claims_in_line(l, i + 1))
            .collect()
    }
}

/// Blank every Python string literal that is not on a DOCSTRING's lines.
///
/// Line structure and columns are kept (`_python_prose`). Text the tokenizer
/// refuses is returned unchanged. f-strings are not blanked (the tokenizer
/// does not call them STRING); the literals nested inside their fields are.
#[must_use]
pub fn python_prose(text: &str) -> String {
    let Some(lexed) = crate::pylex::lex(text) else {
        return text.to_owned();
    };
    let docs = lexed.docstrings();
    let in_doc = |row: usize| docs.iter().any(|&(a, b)| a <= row && row <= b);
    let mut lines: Vec<Vec<char>> = text.split('\n').map(|l| l.chars().collect()).collect();
    let blank = |line: &mut Vec<char>, from: usize, to: usize| {
        for c in line.iter_mut().take(to).skip(from) {
            *c = ' ';
        }
    };
    for &((row, col), (end_row, end_col)) in &lexed.strings {
        if in_doc(row) || row == 0 || end_row > lines.len() {
            continue;
        }
        if row == end_row {
            blank(&mut lines[row - 1], col, end_col);
            continue;
        }
        let len = lines[row - 1].len();
        blank(&mut lines[row - 1], col, len);
        for middle in lines.iter_mut().take(end_row - 1).skip(row) {
            let len = middle.len();
            blank(middle, 0, len);
        }
        blank(&mut lines[end_row - 1], 0, end_col);
    }
    lines
        .into_iter()
        .map(|l| l.into_iter().collect::<String>())
        .collect::<Vec<_>>()
        .join("\n")
}
