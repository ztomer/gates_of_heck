//! A small JavaScript scanner: enough to tell an ES module from a classic script.
//!
//! WHY IT EXISTS. The fallback engine (`JavaScriptCore`) parses a source with
//! `new Function(src)`, which is a CLASSIC script body: a source carrying
//! `import`/`export` is refused by the wrapper, not by the source. Reading that
//! refusal as "the file has a syntax error" would be a false red on every ES
//! module in the estate (apes' `eslint.config.js`, penumbra's `.mjs` files,
//! `media_server`'s `fetch_youtube_cookies.mjs`), so the goal has to be decided
//! BEFORE the parse, from the source's own words.
//!
//! WHAT IT IS NOT. It is not a JavaScript parser. It answers one question --
//! "does this source use module syntax?" -- by walking the source once, skipping
//! strings, template literals and comments, and looking for the two keywords
//! that only a module may carry.
//!
//! WHAT IT MAY GET WRONG, and why it is still the right instrument:
//!
//!   * A REGEX literal is not tracked (`/` is read as division), so a regex
//!     containing a quote can end a string early and make the rest of the line
//!     look like code. The failure is in the SAFE direction for the common
//!     case: the scan may report a module marker that is not one, and the
//!     answer is then "this engine cannot measure it" -- a refusal, never a
//!     false red on a real defect.
//!   * `import(` is a DYNAMIC import, legal in a classic script, so it is not
//!     a module marker. `import.meta` is ESM-only, so it is.
//!
//! The rule it implements, in one line: a source is a module when it carries
//! `import` or `export` where only a module may carry them.

/// What the scanner is reading right now.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    /// JavaScript code.
    Code,
    /// A `// ...` comment, to the end of the line.
    LineComment,
    /// A `/* ... */` comment.
    BlockComment,
    /// A `'...'` string.
    Single,
    /// A `"..."` string.
    Double,
    /// A `` `...` `` template literal, where `${` re-enters code.
    Template,
}

/// Is this source an ES module -- a source that carries `import` or `export`
/// where only a module may carry them?
#[must_use]
pub fn is_module(text: &str) -> bool {
    let chars: Vec<char> = text.chars().collect();
    walk(&chars, 0, Mode::Code, 0, None).0
}

/// Walk the source from `i` in `mode`, and report (a module marker was found,
/// the index just past what was read).
///
/// `depth` counts the braces open in this code context, so the `}` that closes
/// a template literal's `${...}` is found without scanning the whole file
/// again. `last` is the last significant character read, which is how
/// `obj.export` is told from the `export` keyword.
fn walk(
    chars: &[char],
    mut i: usize,
    mut mode: Mode,
    mut depth: usize,
    mut last: Option<char>,
) -> (bool, usize) {
    while i < chars.len() {
        let c = chars[i];
        match mode {
            Mode::Code => {
                match c {
                    '/' if chars.get(i + 1) == Some(&'/') => {
                        mode = Mode::LineComment;
                        i += 2;
                    }
                    '/' if chars.get(i + 1) == Some(&'*') => {
                        mode = Mode::BlockComment;
                        i += 2;
                    }
                    '\'' => {
                        mode = Mode::Single;
                        i += 1;
                    }
                    '"' => {
                        mode = Mode::Double;
                        i += 1;
                    }
                    '`' => {
                        mode = Mode::Template;
                        i += 1;
                    }
                    '{' => {
                        depth += 1;
                        last = Some(c);
                        i += 1;
                    }
                    '}' => {
                        if depth == 0 {
                            // The `}` that closes a template literal's `${...}`.
                            return (false, i + 1);
                        }
                        depth -= 1;
                        last = Some(c);
                        i += 1;
                    }
                    _ if is_ident_start(c) => {
                        let (word, next) = read_word(chars, i);
                        if is_module_keyword(&word, last, chars.get(next)) {
                            return (true, next);
                        }
                        last = word.chars().next_back();
                        i = next;
                    }
                    _ => {
                        if !c.is_whitespace() {
                            last = Some(c);
                        }
                        i += 1;
                    }
                }
            }
            Mode::LineComment => {
                if c == '\n' {
                    mode = Mode::Code;
                }
                i += 1;
            }
            Mode::BlockComment => {
                if c == '*' && chars.get(i + 1) == Some(&'/') {
                    mode = Mode::Code;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            Mode::Single | Mode::Double => {
                let quote = if mode == Mode::Single { '\'' } else { '"' };
                match past_quote(chars, i, quote) {
                    Some(next) => {
                        mode = Mode::Code;
                        last = Some(quote);
                        i = next;
                    }
                    None => i = chars.len(),
                }
            }
            Mode::Template => {
                if c == '\\' {
                    i += 2;
                } else if c == '`' {
                    mode = Mode::Code;
                    last = Some(c);
                    i += 1;
                } else if c == '$' && chars.get(i + 1) == Some(&'{') {
                    // An interpolation is code again, and its `}` ends it.
                    let (found, next) = walk(chars, i + 2, Mode::Code, 1, last);
                    if found {
                        return (true, next);
                    }
                    mode = Mode::Template;
                    i = next;
                } else {
                    i += 1;
                }
            }
        }
    }
    (false, i)
}

/// Is this word a module keyword where it stands?
///
/// `export` is a reserved word, so it is the keyword everywhere except after a
/// `.`, where it is a property name (`obj.export`). `import` is a keyword
/// everywhere except before `(`, which makes it a dynamic import -- legal in a
/// classic script -- and after a `.`, where it is `import.meta`, which is not.
fn is_module_keyword(word: &str, last: Option<char>, next: Option<&char>) -> bool {
    if last == Some('.') {
        // `obj.export` is a property; `import.meta` is ESM-only.
        return word == "import";
    }
    match word {
        "export" => true,
        "import" => !matches!(next, Some('(')),
        _ => false,
    }
}

/// The index just past the closing `quote`, or `None` when the string never closes.
///
/// A backslash escapes the next character, so `\"` does not end the string.
const fn past_quote(chars: &[char], i: usize, quote: char) -> Option<usize> {
    let mut j = i;
    while j < chars.len() {
        if chars[j] == '\\' {
            j += 2;
            continue;
        }
        if chars[j] == quote {
            return Some(j + 1);
        }
        j += 1;
    }
    None
}

/// Read the identifier starting at `i`: (the word, the index just past it).
fn read_word(chars: &[char], i: usize) -> (String, usize) {
    let mut j = i;
    while j < chars.len() && is_ident_part(chars[j]) {
        j += 1;
    }
    (chars[i..j].iter().collect(), j)
}

/// Can an identifier start with this?
fn is_ident_start(c: char) -> bool {
    c.is_alphabetic() || c == '_' || c == '$'
}

/// Can an identifier continue with this?
fn is_ident_part(c: char) -> bool {
    is_ident_start(c) || c.is_ascii_digit()
}

#[cfg(test)]
mod tests {
    use super::is_module;

    #[test]
    fn a_plain_script_is_not_a_module() {
        assert!(!is_module("const a = 1;\nfunction f() { return a; }\n"));
    }

    #[test]
    fn a_dynamic_import_is_not_a_module() {
        assert!(!is_module("const m = await import('./x.js');\n"));
    }

    #[test]
    fn a_static_import_is_a_module() {
        assert!(is_module("import fs from 'node:fs';\n"));
        assert!(is_module("import { a } from './a.js';\n"));
        assert!(is_module("import './side-effect.js';\n"));
        assert!(is_module("import * as ns from './a.js';\n"));
    }

    #[test]
    fn an_export_is_a_module() {
        assert!(is_module("export const x = 1;\n"));
        assert!(is_module("export default function f() {}\n"));
        assert!(is_module("export { a };\n"));
    }

    #[test]
    fn a_keyword_in_a_string_or_comment_is_not_a_module() {
        assert!(!is_module("// import fs from 'node:fs';\nconst a = 1;\n"));
        assert!(!is_module("/* export const x = 1; */\nconst a = 1;\n"));
        assert!(!is_module("const s = 'export const x = 1;';\n"));
        assert!(!is_module("const s = `export const x = 1;`;\n"));
    }

    #[test]
    fn a_property_named_export_is_not_a_module() {
        assert!(!is_module("obj.export = 1;\n"));
    }

    #[test]
    fn import_meta_is_a_module() {
        assert!(is_module("const u = import.meta.url;\n"));
    }

    #[test]
    fn a_keyword_in_a_templates_interpolation_is_read_as_code() {
        assert!(is_module("const s = `${x} ${import.meta.url}`;\n"));
        assert!(!is_module("const s = `${x}`;\n"));
    }
}
