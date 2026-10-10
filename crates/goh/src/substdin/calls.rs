//! The process starters a Python file calls, from its AST (`ruff_python_parser`), and
//! which of them say where the child's stdin comes from.
//!
//! THE INCIDENT, 2026-10-05 (`ZeroThunder`). `tools/check_build_stale.py` resolved its md5
//! command by running each candidate with no input and no `stdin=`. `md5` with no file
//! operand digests STDIN until EOF, so the probe inherited whatever stdin its caller had --
//! and from a terminal, or a pipe whose writer never closes, EOF never comes. It hung the
//! games metarepo's `tools/check_claims.py` until that caller started passing `/dev/null`:
//! the caller was fixed and the gate stayed armed for the next one.
//!
//! THE CLASS is "a child inherits a stdin it did not ask for", and it is not about md5.
//! `cat`, `sed`, `md5sum`, `shasum`, `ffmpeg` (which reads keystrokes), a `bash -c`
//! program lifted out of build.sh, a nested gate suite: any of them reads stdin when it is
//! handed one, and whether that hangs depends on the CALLER, not on the line that is wrong.
//! That is what makes it survive review -- the call works every time it is run from a hook
//! or a test, and hangs the first time someone runs it from a terminal or another tool pipes
//! into it. A list of "tools that read stdin" would be a hand-kept list that rots; so the
//! rule is uniform instead: every `subprocess` call names `stdin=` or `input=`.
//! `stdin=None` is allowed and means "inherit, on purpose" -- the point is that
//! inheriting is a decision someone wrote down, not a default nobody saw.
//!
//! Ported from `ZeroThunder`'s `tools/check_subprocess_stdin.py` (`697ddfe`), which read
//! Python's own `ast`. The same tree, the same findings: this module keeps the AST, its
//! import resolution and its two verdicts, because a gate that drifts from the checker it
//! ports is a second gate. The AST is read for the reason `goh requires-call` reads it
//! (`BACKLOG` 1.1): a call named in a string or a docstring is not a call.
//!
//! ONE LEVEL of attribute, as the reference read it: `mod.attr(...)` with `mod` bound by
//! an `import`, and a bare name bound by a `from ... import`. `import subprocess` +
//! `subprocess.run(...)`, `import subprocess as sp` + `sp.run(...)`, `from subprocess
//! import run as r` + `r(...)` -- every shape the real estate uses. A deeper chain
//! (`a.b.run()`) is deliberately not followed: the reference did not, and reaching through
//! an attribute it never resolved is how a port invents findings nobody has to fix.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;

/// Process starters that accept a `stdin=` keyword, keyed by the module they live in.
const DECLARABLE: [(&str, &[&str]); 2] = [
    (
        "subprocess",
        &["run", "Popen", "call", "check_call", "check_output"],
    ),
    (
        "asyncio",
        &["create_subprocess_exec", "create_subprocess_shell"],
    ),
];

/// Process starters with no way to declare stdin at all. A finding outright: use
/// `subprocess.run(..., stdin=...)`.
const UNDECLARABLE: [(&str, &[&str]); 2] = [
    ("subprocess", &["getoutput", "getstatusoutput"]),
    ("os", &["system", "popen"]),
];

/// The two keywords that say where the child's input comes from. `input=` is
/// `subprocess.run`'s own spelling of the same decision (the bytes it feeds).
const STDIN_KEYWORDS: [&str; 2] = ["stdin", "input"];

/// The module names this check follows an import of. `os` is here for the undeclorable
/// pair; a file that never imports a watched module cannot call one.
fn watched() -> Vec<&'static str> {
    DECLARABLE
        .iter()
        .chain(UNDECLARABLE.iter())
        .map(|(module, _)| *module)
        .collect()
}

/// Why a call leaves stdin undeclared.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Why {
    /// It could have declared one and did not: the inherited-stdin hang.
    Inherits,
    /// It has no way to declare one at all.
    Undeclarable,
}

impl Why {
    /// The word the allowlist's `why` carries and the report shows.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Inherits => "inherits",
            Self::Undeclarable => "undeclarable",
        }
    }
}

/// A process call that leaves stdin undeclared.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// 1-based line the call starts on.
    pub line: usize,
    /// The callee as the file resolved it: `subprocess.run`, `os.system`.
    pub call: String,
    /// Which of the two verdicts this is.
    pub why: Why,
    /// The call's own source line, trimmed: what the ratchet allowlist is keyed on, so
    /// moving the call does not revoke its exemption and DELETING it does.
    pub text: String,
}

impl Finding {
    /// Why it is a finding, in one sentence.
    #[must_use]
    pub fn message(&self) -> String {
        match self.why {
            Why::Undeclarable => {
                format!(
                    "{} cannot declare stdin; use subprocess.run(..., stdin=...)",
                    self.call
                )
            }
            Why::Inherits => {
                format!(
                    "{} inherits the caller's stdin; pass stdin= or input=",
                    self.call
                )
            }
        }
    }
}

/// One call, before the file's import bindings resolve it.
struct Raw {
    offset: usize,
    /// `Some("sp")` for `sp.run()`, `Some("r")` for a bare `r()`; `None` for a callee
    /// that is neither (`a.b.run()`, `f()()`), which no watched binding can reach.
    path: Option<String>,
    /// True for a bare name (a from-import), false for `mod.attr`.
    bare: bool,
    /// The name `attr` binds to, for the attribute case.
    attr: String,
    /// The keyword argument names, `None` for `**kwargs`.
    keywords: Vec<Option<String>>,
}

#[derive(Default)]
struct Collect {
    raw: Vec<Raw>,
    /// `import m as a` / `import m`: binding -> module. Only the watched modules.
    modules: BTreeMap<String, String>,
    /// `from m import f as g`: binding -> `m.f`. Only the watched modules.
    functions: BTreeMap<String, String>,
}

impl<'a> Visitor<'a> for Collect {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    let module = alias.name.to_string();
                    if !watched().contains(&module.as_str()) {
                        continue;
                    }
                    let bound = alias
                        .asname
                        .as_ref()
                        .map_or_else(|| module.clone(), ToString::to_string);
                    self.modules.insert(bound, module);
                }
            }
            Stmt::ImportFrom(from) => {
                let module = from.module.as_ref().map(ToString::to_string);
                let watched_module = module
                    .as_ref()
                    .is_some_and(|m| watched().contains(&m.as_str()));
                if from.level == 0 && watched_module {
                    if let Some(module) = module {
                        for alias in &from.names {
                            let bound = alias
                                .asname
                                .as_ref()
                                .map_or_else(|| alias.name.to_string(), ToString::to_string);
                            self.functions
                                .insert(bound, format!("{module}.{}", alias.name));
                        }
                    }
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Call(call) = expr {
            let keywords = call
                .arguments
                .keywords
                .iter()
                .map(|k| k.arg.as_ref().map(ToString::to_string))
                .collect();
            let raw = match call.func.as_ref() {
                Expr::Name(n) => Some(Raw {
                    offset: usize::from(expr.start()),
                    path: Some(n.id.to_string()),
                    bare: true,
                    attr: n.id.to_string(),
                    keywords,
                }),
                // One level only: `sp.run()` where `sp` is a watched module binding.
                // `a.b.run()` arrives as an Attribute whose value is an Attribute, which is
                // deliberately not followed (see the module docstring).
                Expr::Attribute(a) => match a.value.as_ref() {
                    Expr::Name(n) => Some(Raw {
                        offset: usize::from(expr.start()),
                        path: Some(n.id.to_string()),
                        bare: false,
                        attr: a.attr.to_string(),
                        keywords,
                    }),
                    _ => None,
                },
                _ => None,
            };
            self.raw.extend(raw);
        }
        visitor::walk_expr(self, expr);
    }
}

impl Collect {
    /// `(module, function)` a call resolves to, or `None` when it is not a watched name.
    /// The reference's `_target`: an attribute call needs a module binding, a bare name a
    /// from-import binding, and nothing else can reach.
    fn resolve(&self, raw: &Raw) -> Option<(String, String)> {
        let path = raw.path.as_deref()?;
        if raw.bare {
            self.functions
                .get(path)
                .and_then(|q| q.split_once('.').map(|(m, f)| (m.to_owned(), f.to_owned())))
        } else {
            self.modules
                .get(path)
                .map(|m| (m.clone(), raw.attr.clone()))
        }
    }
}

/// Which of the two verdicts `module.name` gets, or `None` when it is not a process
/// starter this check follows at all.
fn verdict(module: &str, name: &str) -> Option<Why> {
    if UNDECLARABLE
        .iter()
        .any(|(m, names)| *m == module && names.contains(&name))
    {
        return Some(Why::Undeclarable);
    }
    DECLARABLE
        .iter()
        .any(|(m, names)| *m == module && names.contains(&name))
        .then_some(Why::Inherits)
}

/// The text of the line `line` (1-based) occupies, trimmed.
fn line_text(source: &str, line: usize) -> String {
    source
        .split('\n')
        .nth(line.saturating_sub(1))
        .unwrap_or_default()
        .trim()
        .to_owned()
}

/// Every process call `source` makes, and which of them leave stdin undeclared.
///
/// The count is returned alongside rather than derived from the findings, because a repo
/// whose every call is CORRECT reports zero findings and a scan that has lost its subject
/// also reports zero: without the count those two are the same run. This is the floor's
/// subject (`GOH_SUBPROCESS_STDIN_MIN_CALLS`).
///
/// # Errors
///
/// The parser's message when `source` is not Python it can read. A file the check cannot
/// judge is NEVER skipped: the reference reported a `SyntaxError` as a finding, because a
/// tracked `.py` the gate cannot parse is itself the defect, and a scan that quietly drops
/// unreadable files has a scope nobody can account for.
pub fn scan_source(source: &str) -> Result<(Vec<Finding>, usize), String> {
    let parsed = ruff_python_parser::parse_module(source).map_err(|e| e.to_string())?;
    let mut collect = Collect::default();
    collect.visit_body(&parsed.syntax().body);
    let starts: Vec<usize> = std::iter::once(0)
        .chain(source.match_indices('\n').map(|(i, _)| i + 1))
        .collect();
    let line_of = |offset: usize| starts.partition_point(|&s| s <= offset);
    let mut found = Vec::new();
    let mut seen = 0usize;
    for raw in &collect.raw {
        let Some((module, name)) = collect.resolve(raw) else {
            continue;
        };
        let Some(why) = verdict(&module, &name) else {
            continue;
        };
        seen += 1;
        // `**kwargs` alone is a finding: a static read cannot see what the mapping holds,
        // so the caller has to name the decision (`stdin=kwargs.pop("stdin", DEVNULL)`).
        let names_stdin = raw
            .keywords
            .iter()
            .flatten()
            .any(|k| STDIN_KEYWORDS.contains(&k.as_str()));
        if why == Why::Inherits && names_stdin {
            continue;
        }
        let line = line_of(raw.offset);
        found.push(Finding {
            line,
            call: format!("{module}.{name}"),
            why,
            text: line_text(source, line),
        });
    }
    Ok((found, seen))
}

/// Just the findings, for the callers that do not want the count.
#[must_use]
pub fn findings(source: &str) -> Vec<Finding> {
    scan_source(source).map_or_else(|_| Vec::new(), |(found, _)| found)
}

/// One JSON finding set, for `goh subprocess-stdin --source`.
#[must_use]
pub fn findings_json(path: &str, found: &[Finding], seen: usize) -> String {
    let rows: Vec<serde_json::Value> = found
        .iter()
        .map(|f| {
            serde_json::json!({
                "line": f.line,
                "call": f.call,
                "why": f.why.as_str(),
                "message": f.message(),
                "text": f.text,
            })
        })
        .collect();
    let mut s = serde_json::to_string(&serde_json::json!({
        "path": path, "calls": seen, "findings": rows,
    }))
    .unwrap_or_else(|e| format!("{{\"error\": {e:?}}}"));
    s.push('\n');
    s
}

/// The finding lines as the report prints them: `path:line: message`.
#[must_use]
pub fn describe(path: &str, found: &[Finding]) -> String {
    let mut s = String::new();
    for f in found {
        let _ = writeln!(s, "    {path}:{}: {}", f.line, f.message());
    }
    s
}

#[cfg(test)]
mod tests;
