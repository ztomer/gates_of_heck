//! The finder: a `sleep` whose very next statement reads a readiness signal, from the AST.
//!
//! The shape it names is "wait a fixed time, then read whether the thing is ready": a
//! `time.sleep(1)` followed by `proc.poll()`, `proc.returncode`, or a marker file's
//! `exists()`/`read_text()`. On a loaded machine the fixed time is sometimes too short, the read
//! then reads "not ready" for a thing that was merely slow, and the caller acts on that -- a lock
//! read as held, a capture of a frame before the work it was meant to capture. A sleep INSIDE a
//! loop is a poll, not a stand-in, and is not a finding (whether the loop is bounded by a deadline
//! that refuses is a different question, and this check does not pretend to answer it).

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;

/// One finding: the sleep's line, and what the next statement reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub line: usize,
    pub read: String,
}

/// The attribute reads that ask "is it ready yet": a child's state, a marker file's presence or
/// content. Named here once; the module docstring says why each is one.
const PROCESS_READS: [&str; 2] = ["poll", "returncode"];
const FILE_READS: [&str; 6] = [
    "exists",
    "is_file",
    "isfile",
    "read_text",
    "read_bytes",
    "getsize",
];

fn dotted(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(n) => Some(n.id.to_string()),
        Expr::Attribute(a) => dotted(&a.value).map(|base| format!("{base}.{}", a.attr)),
        _ => None,
    }
}

/// Whether `stmt` is a bare `sleep(...)`: `time.sleep`, `asyncio.sleep` awaited, or a `sleep`
/// imported by name. A local function called `sleep` reads the same; the seed names it.
fn is_sleep(stmt: &Stmt) -> bool {
    let Stmt::Expr(e) = stmt else { return false };
    let call = match e.value.as_ref() {
        Expr::Await(a) => a.value.as_ref(),
        other => other,
    };
    let Expr::Call(call) = call else { return false };
    matches!(
        dotted(&call.func).as_deref(),
        Some("time.sleep" | "asyncio.sleep" | "sleep")
    )
}

/// The first readiness read among `exprs`, spelled as the source names it.
#[derive(Default)]
struct Reads(Option<String>);

impl<'a> Visitor<'a> for Reads {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if self.0.is_some() {
            return;
        }
        if let Expr::Attribute(a) = expr {
            let name = a.attr.as_str();
            if PROCESS_READS.contains(&name) || FILE_READS.contains(&name) {
                self.0 = Some(dotted(expr).unwrap_or_else(|| format!(".{name}")));
                return;
            }
        }
        visitor::walk_expr(self, expr);
    }
}

/// The expressions a statement evaluates before it branches: an `if`'s test, an assignment's
/// value, an expression statement, an `assert`'s test, a `return`'s value. A `while` that reads
/// the signal IS the poll, so a sleep before one is a warm-up, not a stand-in.
fn head_exprs(stmt: &Stmt) -> Vec<&Expr> {
    match stmt {
        Stmt::If(s) => vec![s.test.as_ref()],
        Stmt::Assign(s) => vec![s.value.as_ref()],
        Stmt::AnnAssign(s) => s.value.as_deref().into_iter().collect(),
        Stmt::AugAssign(s) => vec![s.value.as_ref()],
        Stmt::Expr(s) => vec![s.value.as_ref()],
        Stmt::Assert(s) => vec![s.test.as_ref()],
        Stmt::Return(s) => s.value.as_deref().into_iter().collect(),
        _ => Vec::new(),
    }
}

fn read_in(stmt: &Stmt) -> Option<String> {
    let mut reads = Reads::default();
    for expr in head_exprs(stmt) {
        reads.visit_expr(expr);
    }
    reads.0
}

/// Where a body sits: a new scope (a `def` or `class`: not in a loop, whatever surrounds it), a
/// loop's own body (where a sleep polls), or a block that keeps whatever surrounds it (an `if`
/// inside a `while` is still inside the loop).
#[derive(Clone, Copy)]
enum Within {
    Scope,
    Loop,
    Same,
}

/// Every body a statement owns, each with where it sits.
fn bodies(stmt: &Stmt) -> Vec<(&[Stmt], Within)> {
    use Within::{Loop, Same, Scope};
    match stmt {
        Stmt::FunctionDef(s) => vec![(&s.body, Scope)],
        Stmt::ClassDef(s) => vec![(&s.body, Scope)],
        Stmt::If(s) => std::iter::once((s.body.as_slice(), Same))
            .chain(
                s.elif_else_clauses
                    .iter()
                    .map(|c| (c.body.as_slice(), Same)),
            )
            .collect(),
        Stmt::For(s) => vec![(&s.body, Loop), (&s.orelse, Same)],
        Stmt::While(s) => vec![(&s.body, Loop), (&s.orelse, Same)],
        Stmt::With(s) => vec![(&s.body, Same)],
        Stmt::Try(s) => {
            let mut out = vec![(s.body.as_slice(), Same)];
            out.extend(s.handlers.iter().map(|h| match h {
                ruff_python_ast::ExceptHandler::ExceptHandler(h) => (h.body.as_slice(), Same),
            }));
            out.push((&s.orelse, Same));
            out.push((&s.finalbody, Same));
            out
        }
        Stmt::Match(s) => s.cases.iter().map(|c| (c.body.as_slice(), Same)).collect(),
        _ => Vec::new(),
    }
}

fn walk(body: &[Stmt], in_loop: bool, out: &mut Vec<(usize, String)>) {
    for (i, stmt) in body.iter().enumerate() {
        if !in_loop && is_sleep(stmt) {
            if let Some(read) = body.get(i + 1).and_then(read_in) {
                out.push((usize::from(stmt.start()), read));
            }
        }
        for (inner, within) in bodies(stmt) {
            let looping = match within {
                Within::Scope => false,
                Within::Loop => true,
                Within::Same => in_loop,
            };
            walk(inner, looping, out);
        }
    }
}

/// Every fixed sleep in `source` that stands in for a readiness signal, in source order.
///
/// # Errors
///
/// The parser's message when `source` is not Python it can read: a file the check cannot judge
/// is named, never passed.
pub fn findings(source: &str) -> Result<Vec<Finding>, String> {
    let parsed = ruff_python_parser::parse_module(source).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    walk(&parsed.syntax().body, false, &mut raw);
    let starts: Vec<usize> = std::iter::once(0)
        .chain(source.match_indices('\n').map(|(i, _)| i + 1))
        .collect();
    Ok(raw
        .into_iter()
        .map(|(offset, read)| Finding {
            line: starts.partition_point(|&s| s <= offset),
            read,
        })
        .collect())
}
