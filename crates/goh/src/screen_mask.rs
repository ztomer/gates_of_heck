//! Swift comment/string masker.
//!
//! One pass: comments and string literals become spaces, newlines always
//! survive (the 1:1 contract callers index against). Block comments nest
//! per Swift's grammar; triple-quoted literals span lines; `\"` escapes
//! stay in-string. Character-indexed, like the reference's list-of-chars
//! scan.

/// Masker states: code, line comment, block comment. An enum, not
/// integers — a state that can only be named cannot be miscompared.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MaskState {
    /// Ordinary source text.
    Code,
    /// To end of line.
    LineComment,
    /// To the matching close (nestable).
    BlockComment,
}

/// Swift source with comments and string literals masked to spaces.
///
/// The 1:1 contract: newlines always survive untouched (callers index the
/// result against `split("\n")` on the original); everything else inside
/// a comment or literal becomes a space. Handles `\"` escapes (the
/// escaped quote stays in-string) and triple-quoted multi-line literals.
/// Block comments nest, per Swift's own grammar.
#[must_use]
pub fn mask_swift(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = chars.clone();
    let n = chars.len();
    let mut i = 0;
    let mut state = MaskState::Code;
    let starts = |i: usize, token: &str| -> bool {
        token.chars().count() <= n - i && chars[i..].iter().collect::<String>().starts_with(token)
    };
    while i < n {
        let ch = chars[i];
        if state == MaskState::Code {
            if ch == '/' && starts(i, "//") {
                out[i] = ' ';
                out[i + 1] = ' ';
                state = MaskState::LineComment;
                i += 2;
            } else if ch == '/' && starts(i, "/*") {
                out[i] = ' ';
                out[i + 1] = ' ';
                state = MaskState::BlockComment;
                i += 2;
            } else if ch == '"' {
                if starts(i, "\"\"\"") {
                    let mut stop = n;
                    let mut k = i + 3;
                    while k + 2 < n + 1 {
                        if k + 2 < n
                            && chars[k] == '"'
                            && chars[k + 1] == '"'
                            && chars[k + 2] == '"'
                        {
                            stop = k + 3;
                            break;
                        }
                        k += 1;
                    }
                    for k in i..stop.min(n) {
                        if chars[k] != '\n' {
                            out[k] = ' ';
                        }
                    }
                    i = stop;
                } else {
                    let mut j = i + 1;
                    while j < n {
                        let cj = chars[j];
                        if cj == '"' || cj == '\n' {
                            break;
                        }
                        if cj == '\\' {
                            j += 1;
                            // An escaped newline does not continue a
                            // single-line literal — let the loop see it.
                            if j < n && chars[j] != '\n' {
                                j += 1;
                            }
                            continue;
                        }
                        j += 1;
                    }
                    let stop = if j < n && chars[j] == '"' { j + 1 } else { j };
                    for k in i..stop {
                        if chars[k] != '\n' {
                            out[k] = ' ';
                        }
                    }
                    i = stop;
                }
            } else {
                i += 1;
            }
        } else if state == MaskState::LineComment {
            if ch == '\n' {
                state = MaskState::Code;
            } else {
                out[i] = ' ';
            }
            i += 1;
        } else if ch == '\n' {
            // Newlines survive every state — the 1:1 contract.
            if state == MaskState::LineComment {
                state = MaskState::Code;
            }
            i += 1;
        } else if state == MaskState::BlockComment {
            if starts(i, "/*") {
                out[i] = ' ';
                out[i + 1] = ' ';
                i += 2;
            } else if starts(i, "*/") {
                out[i] = ' ';
                out[i + 1] = ' ';
                state = MaskState::Code;
                i += 2;
            } else {
                out[i] = ' ';
                i += 1;
            }
        } else {
            i += 1;
        }
    }
    out.into_iter().collect()
}
