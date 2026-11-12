//! The finder: a `(s)`, `(es)` or `(ies)` glued to a word INSIDE a string literal.
//!
//! Line-based, as `goh status-markers` is: a whole-line comment is prose about a string, not a
//! string, and a trailing comment ends the code that is read. Which quotes open a string depends
//! on the language: `"` everywhere, `'` too in Python and shell (in Rust and Swift a `'` is a char
//! literal or a lifetime). A hedge must follow a letter or a `}` (`{unit}(s)` is a hedge built
//! from a placeholder). Not a hedge, by what it is: `http(s)` (a scheme), a call in a code
//! fragment held in a string (`console.log(s)`, `module_from_spec(s)`, `def close(s):` -- a word
//! after `.`, `def `, `fn ` or `func `, or one with `_` in it), and a builtin shown called
//! (`print(s)`, `len(s)`: `CALLS`, GOH.HEDGECODE). What the lexing misses -- a
//! hedge inside a multi-line string's middle lines (a heredoc, a docstring body) -- is invisible
//! here, and said so in `mod.rs`.

/// The language a file is lexed as: which quote characters open a string, and its comment opener.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lang {
    /// Python and shell: `"` and `'` strings, `#` comments.
    Hash,
    /// Rust and Swift: `"` strings, `//` comments.
    Slash,
}

fn is_comment_line(line: &str, lang: Lang) -> bool {
    let t = line.trim_start();
    match lang {
        Lang::Hash => t.starts_with('#') && !t.starts_with("#!"),
        Lang::Slash => {
            t.starts_with("//") || t.starts_with("/*") || t.starts_with("* ") || t == "*"
        }
    }
}

/// Where the lexer is: in code (a substitution's own `(` depth), or in a string.
enum Ctx {
    Code(usize),
    Str(char, usize),
}

/// The byte spans of `line`'s string literals' contents, up to a trailing comment.
///
/// In Python and shell a `$(` inside a `"` string is a command substitution: its code is not
/// string text, and the strings inside it are strings (`"$([[ "$x" == *"5 file(s)"* ]])"` hedges;
/// a naive quote toggle read that hedge as code, scripts SC-3, 2026-11-12).
#[must_use]
pub fn literal_spans(line: &str, lang: Lang) -> Vec<(usize, usize)> {
    if is_comment_line(line, lang) {
        return Vec::new();
    }
    let mut spans = Vec::new();
    let mut stack = vec![Ctx::Code(0)];
    let mut escaped = false;
    let mut opener = false;
    let mut prev = ' ';
    for (i, c) in line.char_indices() {
        let nested = stack.len() > 1;
        match stack.last_mut() {
            Some(Ctx::Str(q, start)) => {
                if escaped {
                    escaped = false;
                } else if c == '\\' {
                    escaped = true;
                } else if c == *q {
                    spans.push((*start, i));
                    stack.pop();
                } else if lang == Lang::Hash
                    && *q == '"'
                    && c == '$'
                    && line[i + 1..].starts_with('(')
                {
                    spans.push((*start, i));
                    stack.push(Ctx::Code(0));
                    opener = true;
                }
            }
            Some(Ctx::Code(depth)) => {
                if c == '"' || (c == '\'' && lang == Lang::Hash) {
                    stack.push(Ctx::Str(c, i + 1));
                } else if !nested
                    && ((lang == Lang::Hash && c == '#' && (prev.is_whitespace() || i == 0))
                        || (lang == Lang::Slash && c == '/' && line[i + 1..].starts_with('/')))
                {
                    break; // a comment starts: nothing after it is a string
                } else if c == '(' {
                    if opener {
                        opener = false;
                    } else {
                        *depth += 1;
                    }
                } else if c == ')' && nested {
                    if *depth == 0 {
                        stack.pop(); // the substitution ends: its string goes on
                        if let Some(Ctx::Str(_, start)) = stack.last_mut() {
                            *start = i + 1;
                        }
                    } else {
                        *depth -= 1;
                    }
                }
            }
            None => break,
        }
        prev = c;
    }
    if let Some(Ctx::Str(_, start)) = stack.last() {
        spans.push((*start, line.len())); // a string that runs on past the line's end
    }
    spans
}

/// Builtin calls a string shows as code (`print(s)`, `len(s)`): no count noun is spelled so. A
/// builtin that is also a count noun (`set`, `list`, `type`, `id`, `map`) is not here: `3 set(s)`
/// is a hedge.
const CALLS: &[&str] = &[
    "abs",
    "bool",
    "callable",
    "chr",
    "enumerate",
    "eval",
    "exec",
    "float",
    "getattr",
    "hasattr",
    "int",
    "isinstance",
    "len",
    "ord",
    "print",
    "repr",
    "reversed",
    "setattr",
    "sorted",
    "str",
    "vars",
    "zip",
];

/// A name shown CALLED, not a noun hedged (GOH.HEDGECALL: `CadGoose2`'s C fixture string held
/// `stripThinkBlocks(s);`): a `lowerCamelCase` identifier, a function's spelling (a type's `PascalCase`
/// stays a noun: necrohand's `CAMetalLayer(s)` hedges, as do `URL(s)` and `File(s)`), or any name whose `(s)` is followed at once by
/// what only code puts there -- a method call `.name`, or a `;` closing a block (`; }`). A noun
/// hedge is followed by a space, a `{placeholder}`, `,`, `)`, `:`, `;` or a sentence's `.`.
fn is_identifier_call(word: &str, after: &str) -> bool {
    let camel = word.starts_with(|c: char| c.is_lowercase())
        && word
            .chars()
            .zip(word.chars().skip(1))
            .any(|(a, b)| a.is_lowercase() && b.is_uppercase());
    let mut rest = after.chars();
    let next = rest.next();
    let code = match next {
        Some('.') => rest.next().is_some_and(|c| c.is_alphabetic() || c == '_'),
        // `f(s); }` ends a statement in a block; `entry(s); then` and `app(s); "` are prose.
        Some(';') => {
            let first = rest.next();
            first == Some('}') || (first == Some(' ') && rest.next() == Some('}'))
        }
        _ => false,
    };
    camel || code
}

/// The hedges in one piece of string text: each `word(s)` as written.
#[must_use]
pub fn hedges(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for suffix in ["(s)", "(es)", "(ies)"] {
        for (at, _) in text.match_indices(suffix) {
            let before = &text[..at];
            let Some(last) = before.chars().next_back() else {
                continue;
            };
            if !(last.is_alphabetic() || last == '}') {
                continue;
            }
            let start = before
                .char_indices()
                .rev()
                .find(|(_, c)| !(c.is_alphanumeric() || *c == '_' || *c == '{' || *c == '}'))
                .map_or(0, |(i, c)| i + c.len_utf8());
            let word = &before[start..];
            let lead = &before[..start];
            if word.eq_ignore_ascii_case("http")
                || (suffix == "(s)" && CALLS.contains(&word))
                || (suffix == "(s)" && is_identifier_call(word, &text[at + suffix.len()..]))
                || (word.contains('_') && !word.contains('{'))
                || lead.ends_with('.')
                || ["def ", "fn ", "func "].iter().any(|k| lead.ends_with(k))
            {
                continue;
            }
            out.push(format!("{word}{suffix}"));
        }
    }
    out
}

/// One finding: its line and the hedge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub hedge: String,
}

/// Every line of `source` whose string literals hedge a plural, the first hedge named.
#[must_use]
pub fn findings(source: &str, lang: Lang) -> Vec<Finding> {
    let mut out = Vec::new();
    for (i, line) in source.lines().enumerate() {
        let first = literal_spans(line, lang)
            .into_iter()
            .find_map(|(a, b)| hedges(&line[a..b]).into_iter().next());
        if let Some(hedge) = first {
            out.push(Finding { line: i + 1, hedge });
        }
    }
    out
}
