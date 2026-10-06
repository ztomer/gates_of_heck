//! Comment, string-literal and heredoc blanking for the three languages the
//! unreaped-spawn scan reads. Port of the retired `checks/_spawn_mask.py`.
//!
//! ONE PASS, comments and strings together: stripping comments first lets a
//! `#` inside a string eat real code, and a `//` inside a string open a
//! comment that swallows the rest of the file. Every function preserves the
//! line count and the character offsets, so a finding names the line the
//! author wrote. Work is in `char`s, as the reference's is, so a multi-byte
//! character blanks to exactly one space.

/// `[rRbBfFuU]` -- a Python string prefix character.
const fn py_prefix(c: char) -> bool {
    matches!(c, 'r' | 'R' | 'b' | 'B' | 'f' | 'F' | 'u' | 'U')
}

fn starts(text: &[char], at: usize, pat: &str) -> bool {
    pat.chars()
        .enumerate()
        .all(|(d, p)| text.get(at + d) == Some(&p))
}

fn blank(out: &mut String, c: char) {
    out.push(if c == '\n' { '\n' } else { ' ' });
}

fn spaces(out: &mut String, n: usize) {
    out.extend(std::iter::repeat_n(' ', n));
}

/// `_PY_STR_OPEN.match(text, i)`: (opener length, closing quote), trying the
/// longest prefix first the way the backtracking engine does.
fn py_open(text: &[char], i: usize) -> Option<(usize, &'static str)> {
    for k in (0..=2usize).rev() {
        if (0..k).all(|d| text.get(i + d).copied().is_some_and(py_prefix)) {
            for q in ["\"\"\"", "'''", "\"", "'"] {
                if starts(text, i + k, q) {
                    return Some((k + q.chars().count(), q));
                }
            }
        }
    }
    None
}

/// Python: comments and string bodies blanked. A backslash escapes the
/// closing quote even in a raw literal (measured against `CPython`).
#[must_use]
pub fn mask_py(src: &str) -> String {
    let text: Vec<char> = src.chars().collect();
    let n = text.len();
    let mut out = String::with_capacity(src.len());
    let (mut i, mut quote, mut escape) = (0usize, "", false);
    while i < n {
        let ch = text[i];
        if !quote.is_empty() {
            if escape {
                escape = false;
                blank(&mut out, ch);
                i += 1;
            } else if ch == '\\' {
                escape = true;
                out.push(' ');
                i += 1;
            } else if starts(&text, i, quote) {
                let len = quote.chars().count();
                spaces(&mut out, len);
                i += len;
                quote = "";
            } else {
                blank(&mut out, ch);
                i += 1;
            }
            continue;
        }
        if ch == '#' {
            while i < n && text[i] != '\n' {
                out.push(' ');
                i += 1;
            }
            continue;
        }
        if let Some((len, q)) = py_open(&text, i) {
            quote = q;
            spaces(&mut out, len);
            i += len;
            continue;
        }
        out.push(ch);
        i += 1;
    }
    spaces(&mut out, quote.chars().count());
    out
}

/// `_RS_RAW_OPEN.match`: `[bcr]?r(#*)"` -> (opener length, hash count).
fn rs_raw_open(text: &[char], i: usize) -> Option<(usize, usize)> {
    let at_r = |j: usize| -> Option<(usize, usize)> {
        if text.get(j) != Some(&'r') {
            return None;
        }
        let mut k = j + 1;
        while text.get(k) == Some(&'#') {
            k += 1;
        }
        (text.get(k) == Some(&'"')).then(|| (k + 1 - i, k - j - 1))
    };
    if matches!(text.get(i), Some('b' | 'c' | 'r')) {
        if let Some(hit) = at_r(i + 1) {
            return Some(hit);
        }
    }
    at_r(i)
}

/// Rust: `//`, nestable `/* */` and every string form blanked.
///
/// Char literals are left alone (`'a` is a lifetime far more often than a character). The
/// closing delimiter of a raw string is the quote plus its hashes, and a
/// backslash means nothing inside one.
#[must_use]
pub fn mask_rust(src: &str) -> String {
    let text: Vec<char> = src.chars().collect();
    let n = text.len();
    let mut out = String::with_capacity(src.len());
    let (mut i, mut depth, mut escape, mut raw) = (0usize, 0usize, false, false);
    let mut close = String::new();
    while i < n {
        if !close.is_empty() {
            if escape {
                escape = false;
                blank(&mut out, text[i]);
                i += 1;
            } else if text[i] == '\\' && !raw {
                escape = true;
                out.push(' ');
                i += 1;
            } else if starts(&text, i, &close) {
                let len = close.chars().count();
                spaces(&mut out, len);
                i += len;
                close.clear();
            } else {
                blank(&mut out, text[i]);
                i += 1;
            }
            continue;
        }
        if depth > 0 {
            if starts(&text, i, "*/") {
                depth -= 1;
                spaces(&mut out, 2);
                i += 2;
            } else if starts(&text, i, "/*") {
                depth += 1;
                spaces(&mut out, 2);
                i += 2;
            } else {
                blank(&mut out, text[i]);
                i += 1;
            }
            continue;
        }
        if starts(&text, i, "//") {
            while i < n && text[i] != '\n' {
                out.push(' ');
                i += 1;
            }
            continue;
        }
        if starts(&text, i, "/*") {
            depth = 1;
            spaces(&mut out, 2);
            i += 2;
            continue;
        }
        if let Some((len, hashes)) = rs_raw_open(&text, i) {
            close = format!("\"{}", "#".repeat(hashes));
            raw = true;
            spaces(&mut out, len);
            i += len;
            continue;
        }
        let str_len = if matches!(text[i], 'b' | 'c') && text.get(i + 1) == Some(&'"') {
            2
        } else {
            usize::from(text[i] == '"')
        };
        if str_len > 0 {
            "\"".clone_into(&mut close);
            raw = false;
            spaces(&mut out, str_len);
            i += str_len;
            continue;
        }
        out.push(text[i]);
        i += 1;
    }
    out
}

/// The tag a heredoc on this line opens: the OPERATOR located in the masked
/// line (not in quotes, not a here-string's `<<<`), the tag read from the
/// RAW line -- a quoted `<<'EOF'` is blank in the masked one.
fn heredoc_tag(masked: &str, raw: &str, tag_rx: &regex::Regex) -> Option<String> {
    let m: Vec<char> = masked.chars().collect();
    let r: Vec<char> = raw.chars().collect();
    let mut i = 0usize;
    while i + 1 < m.len() {
        let op = m[i] == '<'
            && m[i + 1] == '<'
            && (i == 0 || m[i - 1] != '<')
            && m.get(i + 2) != Some(&'<');
        if op {
            let mut end = i + 2;
            if m.get(end) == Some(&'-') {
                end += 1;
            }
            let rest: String = r.get(end..).unwrap_or(&[]).iter().collect();
            if let Some(tag) = tag_rx.captures(&rest).and_then(|c| c.get(1)) {
                return Some(tag.as_str().to_owned());
            }
        }
        i += 1;
    }
    None
}

/// Shell: comments, quoted spans and heredoc bodies. A heredoc body holding a
/// `&` is data, not a background launch.
#[must_use]
pub fn mask_shell(src: &str, heredoc_rx: &regex::Regex) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut heredoc: Option<String> = None;
    for line in src.split('\n') {
        let width = line.chars().count();
        if let Some(tag) = &heredoc {
            let done = line.trim() == tag;
            out.push(" ".repeat(width));
            if done {
                heredoc = None;
            }
            continue;
        }
        let chars: Vec<char> = line.chars().collect();
        let mut code: Vec<char> = Vec::with_capacity(chars.len());
        let mut quote: Option<char> = None;
        for (i, &ch) in chars.iter().enumerate() {
            if let Some(q) = quote {
                if ch == q {
                    quote = None;
                }
                code.push(' ');
            } else if ch == '"' || ch == '\'' {
                quote = Some(ch);
                code.push(' ');
            } else if ch == '#' && code.last().is_none_or(|c| " \t;|&(".contains(*c)) {
                code.extend(std::iter::repeat_n(' ', chars.len() - i));
                break;
            } else {
                code.push(ch);
            }
        }
        let joined: String = code.into_iter().collect();
        heredoc = heredoc_tag(&joined, line, heredoc_rx);
        out.push(joined);
    }
    out.join("\n")
}
