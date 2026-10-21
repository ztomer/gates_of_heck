//! The template rule: a template is judged by its SUBSTITUTED form.
//!
//! WHY A TEMPLATE IS NOT A PLAIN FILE. A template carries placeholders -- the
//! parts whose value is filled in later. Those parts are not JavaScript, so the
//! file as it stands is not JavaScript either: `const timeout = ${TIMEOUT};` is
//! a template, and it does not parse. Two ways to deal with that, and one of
//! them is the defect this rule exists to prevent:
//!
//!   * EXCLUDE the template from the check. The file is then never parsed, ever,
//!     and a template whose skeleton is broken -- an unbalanced brace, a stray
//!     backtick, a missing semicolon -- is a defect nobody sees. An exclusion
//!     hides the subject, and the subject is the reason the gate exists.
//!   * SUBSTITUTE each placeholder with a neutral expression, and parse THAT.
//!     The skeleton is judged; the placeholders are not.
//!
//! THE SUBSTITUTION IS NARROW ON PURPOSE. Only the placeholders the tree's own
//! convention defines are replaced, and only the ones that cannot be JavaScript
//! anyway:
//!
//!   * `${NAME}` -- a simple name. The estate's own form (`media_server`'s
//!     `env-mini-rs` render lists exactly these), and a name is always a valid
//!     expression, so nothing can hide behind it.
//!   * `@NAME@` -- the at-marker form (`media_server`'s and `adguard`'s
//!     `install.sh.tmpl`).
//!
//! A hole with an EXPRESSION in it (`${window.TIMEOUT}`, `${a + b}`) is left
//! alone: it is JavaScript, and the substituted form still carries it, so the
//! parse still judges it. A malformed hole (`${a b}`, `${a`, `${a b c}`) is not
//! a placeholder either, so it is left alone too -- and the parse then fails on
//! it, which is the finding. Nothing is substituted into a hole that could
//! have carried a defect.
//!
//! WHAT A TEMPLATE IS, by the tree's own convention: a file whose NAME says so
//! -- `templates_*.js` (divoom-control's web UI), `*.tmpl.js`, `*.template.js`.
//! The convention is in the name, not in a rule this gate invented, and it is
//! printed in the report, so a reader can see which files were judged as
//! templates and which as plain source.

/// The neutral expression every placeholder is replaced with.
///
/// A literal, so it is an expression wherever an expression may stand, and it
/// is a VALUE, so it cannot change the shape of what surrounds it.
pub const NEUTRAL: &str = "0";

/// The template convention, by file name: the stem starts with `template`.
const TEMPLATE_STEM: &str = "template";

/// The other two spellings of the same convention: `name.tmpl.js`, `name.template.js`.
const TEMPLATE_MARKS: [&str; 2] = [".tmpl.", ".template."];

/// Is this path a template, by the tree's own naming convention?
#[must_use]
pub fn is_template(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);
    let stem = name.split('.').next().unwrap_or(name);
    stem.to_ascii_lowercase().starts_with(TEMPLATE_STEM)
        || TEMPLATE_MARKS.iter().any(|mark| name.contains(mark))
}

/// What the substitution did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Substituted {
    /// The text to parse: the source with every placeholder replaced.
    pub text: String,
    /// How many placeholders were replaced.
    pub count: usize,
    /// The names replaced, in the order they were found (for the report).
    pub names: Vec<String>,
}

/// Replace every placeholder in `text` with the neutral expression.
///
/// The scan is textual, and that is safe for the question being asked: a
/// placeholder is a name (`${NAME}`, `@NAME@`), so replacing it cannot break a
/// string, a comment or a regex -- it can only put a literal where a name was.
#[must_use]
pub fn substitute(text: &str) -> Substituted {
    let mut out = String::with_capacity(text.len());
    let mut names = Vec::new();
    let bytes: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < bytes.len() {
        if let Some((name, next)) = braced(&bytes, i) {
            names.push(name);
            out.push_str(NEUTRAL);
            i = next;
            continue;
        }
        if let Some((name, next)) = at_marker(&bytes, i) {
            names.push(name);
            out.push_str(NEUTRAL);
            i = next;
            continue;
        }
        out.push(bytes[i]);
        i += 1;
    }
    Substituted {
        count: names.len(),
        names,
        text: out,
    }
}

/// `${NAME}` at `i`: the name, and the index just past the closing brace.
fn braced(chars: &[char], i: usize) -> Option<(String, usize)> {
    if chars.get(i) != Some(&'$') || chars.get(i + 1) != Some(&'{') {
        return None;
    }
    let mut j = i + 2;
    while j < chars.len() && is_name_part(chars[j]) {
        j += 1;
    }
    if j == i + 2 || chars.get(j) != Some(&'}') {
        return None;
    }
    Some((chars[i + 2..j].iter().collect(), j + 1))
}

/// `@NAME@` at `i`: the name, and the index just past the closing at.
fn at_marker(chars: &[char], i: usize) -> Option<(String, usize)> {
    if chars.get(i) != Some(&'@') {
        return None;
    }
    let mut j = i + 1;
    while j < chars.len() && is_name_part(chars[j]) {
        j += 1;
    }
    if j == i + 1 || chars.get(j) != Some(&'@') {
        return None;
    }
    Some((chars[i + 1..j].iter().collect(), j + 1))
}

/// Can a placeholder's name hold this?
fn is_name_part(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

#[cfg(test)]
mod tests {
    use super::{is_template, substitute};

    #[test]
    fn the_convention_is_the_tree_s_own() {
        assert!(is_template("divoom_gui/web_ui/templates_settings.js"));
        assert!(is_template("web/templates.js"));
        assert!(is_template("web/page.tmpl.js"));
        assert!(is_template("web/page.template.js"));
        assert!(!is_template("web/app_globals.js"));
        assert!(!is_template("web/attestation.js"));
    }

    #[test]
    fn a_braced_name_is_replaced() {
        let out = substitute("const timeout = ${TIMEOUT};\n");
        assert_eq!(out.text, "const timeout = 0;\n");
        assert_eq!(out.count, 1);
        assert_eq!(out.names, vec!["TIMEOUT".to_owned()]);
    }

    #[test]
    fn an_at_marker_is_replaced() {
        let out = substitute("const timeout = @TIMEOUT@;\n");
        assert_eq!(out.text, "const timeout = 0;\n");
        assert_eq!(out.count, 1);
    }

    #[test]
    fn a_hole_with_an_expression_is_left_alone() {
        // It is JavaScript, not a placeholder: substituting it would hide the
        // hole's own validity.
        let out = substitute("const t = `${window.TIMEOUT}`;\n");
        assert_eq!(out.text, "const t = `${window.TIMEOUT}`;\n");
        assert_eq!(out.count, 0);
    }

    #[test]
    fn a_malformed_hole_is_left_alone() {
        // Not a placeholder, so the parse still judges it -- and fails.
        let out = substitute("const t = `${a b}`;\n");
        assert_eq!(out.count, 0);
        let out = substitute("const t = `${a`;\n");
        assert_eq!(out.count, 0);
    }

    #[test]
    fn a_dynamic_import_is_not_a_placeholder() {
        let out = substitute("const m = await import('./x.js');\n");
        assert_eq!(out.count, 0);
    }

    #[test]
    fn a_simple_name_hole_is_a_placeholder() {
        // A name is always a valid expression, so replacing it cannot hide a
        // defect behind it -- and a template whose hole is a name is exactly
        // the template that is not JavaScript on its own.
        let out = substitute("const t = `${a}`;\n");
        assert_eq!(out.text, "const t = `0`;\n");
        assert_eq!(out.count, 1);
    }

    #[test]
    fn plain_source_is_untouched_by_a_name() {
        // Nothing but a NAME is a placeholder: an expression hole, a dynamic
        // import and a string are all left exactly as they were.
        let text = "const a = 1;\nfunction f() { return `${a.b}`; }\n";
        assert_eq!(substitute(text).text, text);
    }
}
