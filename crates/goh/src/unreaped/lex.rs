//! The shared VOCABULARY of the unreaped-spawn scan. Port of
//! the retired `checks/_spawn_lex.py` plus the patterns of the Python and shell rules.
//!
//! The `regex` crate has no look-around, so the reference's look-behinds are
//! applied by hand with the reference's own semantics: the pattern is tried
//! ANCHORED at every position whose preceding character passes, which is what
//! a backtracking engine does with a leading `(?<!...)`. A pattern built at
//! scan time (one naming a binding or a guard type) is compiled once and kept;
//! a pattern that fails to compile is recorded and makes the scan exit 2,
//! never read as "no match".

use std::cell::RefCell;
use std::collections::HashMap;

use regex::Regex;

/// A crate that already ships the guard this gate asks people to write.
pub const EXTERNAL_GUARDS: [&str; 2] = ["assert_cmd::Command", "escargot::Command"];

/// A CALL on the child handle that reaps it by contract (a helper whose whole
/// contract is stop-or-wait), not by this file's own code.
pub const EXTERNAL_REAPERS: [&str; 10] = [
    "wait_timeout",
    "wait_for_output",
    "wait_for_exit",
    "wait_child",
    "kill_and_wait",
    "stop_and_wait",
    "terminate_and_wait",
    "reap_child",
    "spawn_guarded",
    "guard_child",
];

/// Python's `\w` for the look-behind checks.
#[must_use]
pub fn word(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Every compiled pattern of one scan.
pub struct Lex {
    pub spawn: Regex,
    pub cmd_new: Regex,
    pub func: Regex,
    pub panic_lb: Regex,
    pub panic_rest: Regex,
    pub let_bind: Regex,
    pub watchdog: Regex,
    pub killer: Regex,
    pub drop_impl: Regex,
    pub impl_type: Regex,
    pub receiver: Regex,
    pub stop: Regex,
    pub collect: Regex,
    pub guard_body: Regex,
    pub stmt_end_ret: Regex,
    pub return_head: Regex,
    pub semi_brace: Regex,
    pub semi_brace_open: Regex,
    pub self_ctor: Regex,
    pub foreign_let: Regex,
    pub known_types: Regex,
    pub mod_or_close: Regex,
    pub heredoc: Regex,
    pub py_popen: Regex,
    pub py_reap: Regex,
    pub py_with: Regex,
    pub py_panic: Regex,
    pub py_bind: Regex,
    pub py_return: Regex,
    pub py_self_attr: Regex,
    pub py_dotted_popen: Regex,
    pub sh_fn: Regex,
    pub sh_reap: Regex,
    pub sh_trap: Regex,
    built: RefCell<HashMap<String, Regex>>,
    errors: RefCell<Vec<String>>,
}

fn rx(pattern: &str) -> Result<Regex, String> {
    Regex::new(pattern).map_err(|e| format!("unreaped-spawn pattern {pattern:?}: {e}"))
}

impl Lex {
    /// Compile the vocabulary.
    ///
    /// # Errors
    /// A built-in pattern that does not compile, named.
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            spawn: rx(r"\.\s*spawn\s*\(")?,
            cmd_new: rx(r"\b(?:std::process::)?Command\s*::\s*new\b|\bCommand\s*::\s*new\b")?,
            func: rx(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)",
            )?,
            // PANICS' first alternative, whose `(?<![\w:])` is applied by hand.
            panic_lb: rx(r"^(?:(?:debug_)?assert(?:_eq|_ne)?!|panic!|unreachable!|todo!)\s*\(")?,
            panic_rest: rx(
                r"\bprocess::abort\s*\(|\.\s*(?:unwrap|unwrap_or|unwrap_or_else|unwrap_err|expect)\s*\(|\bstd::process::exit\s*\(|\breturn\b|\?[\s,;)]*$",
            )?,
            let_bind: rx(r"\blet\s+(?:mut\s+)?([A-Za-z_][A-Za-z0-9_]*)\b")?,
            watchdog: rx(r"thread::spawn|std::thread::spawn")?,
            killer: rx(r"libc::kill\s*\(|\bkill\s*\(|SIGKILL")?,
            drop_impl: rx(r"\bimpl(?:\s*<[^>]*>)?\s+Drop\s+for\s+([A-Za-z_][A-Za-z0-9_]*)[^{]*\{")?,
            impl_type: rx(r"^\s*impl(?:\s*<[^>]*>)?\s+(?:.*\bfor\s+)?([A-Za-z_][A-Za-z0-9_]*)")?,
            receiver: rx(r"([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*spawn\s*\(")?,
            stop: rx(r"\.\s*(?:kill|start_kill|terminate|send_signal)\s*\(")?,
            collect: rx(r"\.\s*(?:wait|wait_with_output|wait_timeout|try_wait)\s*\(")?,
            guard_body: rx(
                r"\.\s*(?:kill|wait|wait_with_output|wait_timeout|try_wait|terminate)\s*\(",
            )?,
            stmt_end_ret: rx(r"^\s*(return\b|})")?,
            return_head: rx(r"^\s*return\b")?,
            semi_brace: rx(r"[;}]")?,
            semi_brace_open: rx(r"[;{}]")?,
            self_ctor: rx(r"\bSelf\s*[\{\(]")?,
            foreign_let: rx(
                r"\blet\s+(?:mut\s+)?\w+\s*(?::[^=;\n]+)?=\s*([A-Z][A-Za-z0-9_]*)\s*(?:::|\()",
            )?,
            known_types: rx(r"\b(?:struct|enum|union|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)")?,
            mod_or_close: rx(r"^\s*mod\b|^\s*}")?,
            heredoc: rx(r#"^\s*['"]?([A-Za-z_][A-Za-z0-9_]*)"#)?,
            // PY_POPEN, whose `(?<![\w.])` is applied by hand.
            py_popen: rx(r"^(?:subprocess\s*\.\s*)?Popen\s*\(")?,
            py_reap: rx(r"\.\s*(?:wait|communicate|kill|terminate)\s*\(")?,
            py_with: rx(r"^\s*with\b|\bwith\b")?,
            py_panic: rx(
                r"^\s*(?:assert|raise\b|return\b|self\.fail\b|pytest\.fail\b|.*\.unwrap\s*\()|sys\.exit\s*\(",
            )?,
            py_bind: rx(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:subprocess\s*\.\s*)?Popen\s*\(")?,
            py_return: rx(r"^\s*return\b")?,
            py_self_attr: rx(r"^\s*(?:self|cls)\.\w+\s*=")?,
            py_dotted_popen: rx(r"^\s*\w+\.\w+\s*=\s*(?:subprocess\s*\.\s*)?Popen\s*\(")?,
            sh_fn: rx(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*\{\s*$")?,
            sh_reap: rx(r"^(?:wait|trap|kill)\b")?,
            sh_trap: rx(r"^trap\b")?,
            built: RefCell::new(HashMap::new()),
            errors: RefCell::new(Vec::new()),
        })
    }

    /// Does an anchored pattern match at some position whose preceding
    /// character is not `forbidden`? A leading `(?<![...])`, by hand.
    pub fn search_after(anchored: &Regex, text: &str, forbidden: impl Fn(char) -> bool) -> bool {
        let mut prev: Option<char> = None;
        for (at, c) in text.char_indices() {
            if !prev.is_some_and(&forbidden) && anchored.is_match(&text[at..]) {
                return true;
            }
            prev = Some(c);
        }
        false
    }

    /// `PANICS.search(line)`.
    pub fn panics(&self, line: &str) -> bool {
        self.panic_rest.is_match(line)
            || Self::search_after(&self.panic_lb, line, |c| word(c) || c == ':')
    }

    /// `RETURNS.search(line)`: an `->` not followed by `()` (spaces allowed
    /// anywhere: `-> ()` returns nothing too).
    pub fn returns(&self, line: &str) -> bool {
        let unit = self.cached(r"^\s*\(\s*\)");
        line.match_indices("->")
            .any(|(at, _)| !unit.as_ref().is_some_and(|u| u.is_match(&line[at + 2..])))
    }

    /// `PY_POPEN.search(line)`.
    pub fn py_popen(&self, line: &str) -> bool {
        Self::search_after(&self.py_popen, line, |c| word(c) || c == '.')
    }

    /// `SH_REAPS.search(text)`.
    pub fn sh_reaps(&self, text: &str) -> bool {
        Self::search_after(&self.sh_reap, text, word)
    }

    /// `(?<![\w])trap\b` searched.
    pub fn sh_traps(&self, text: &str) -> bool {
        Self::search_after(&self.sh_trap, text, word)
    }

    /// A pattern built at scan time, compiled once.
    pub fn cached(&self, pattern: &str) -> Option<Regex> {
        if let Some(hit) = self.built.borrow().get(pattern) {
            return Some(hit.clone());
        }
        match Regex::new(pattern) {
            Ok(compiled) => {
                self.built
                    .borrow_mut()
                    .insert(pattern.to_owned(), compiled.clone());
                Some(compiled)
            }
            Err(e) => {
                self.errors
                    .borrow_mut()
                    .push(format!("unreaped-spawn pattern {pattern:?}: {e}"));
                None
            }
        }
    }

    /// `re.search(pattern, text)` for a pattern built at scan time.
    pub fn found(&self, pattern: &str, text: &str) -> bool {
        self.cached(pattern).is_some_and(|r| r.is_match(text))
    }

    /// The patterns that could not be compiled, if any: the scan's verdict
    /// is then not a verdict.
    pub fn errors(&self) -> Vec<String> {
        self.errors.borrow().clone()
    }
}
