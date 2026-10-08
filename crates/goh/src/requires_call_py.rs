//! The calls a Python file makes, from its AST (`ruff_python_parser`), for `goh requires-call`.
//!
//! Sized for BACKLOG 1.1 (2026-10-08): over `ZoneWM`'s `tools/` and this repo's 457 Python files it
//! found the same 29,973 call sites, line for line, as Python's own `ast`, in 0.10 s. A call named
//! in a string or a docstring is not a call -- the reason the check reads the AST at all.

use std::collections::BTreeMap;

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;

/// One call site: its line, the name it calls last (`f` in `f()` and `a.b.f()`), and, when the
/// callee resolves through an import binding, `module.function` (`import m as a; a.f()`,
/// `from m import f as g; g()`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Site {
    pub line: usize,
    pub last: String,
    pub qualified: Option<String>,
}

impl Site {
    /// `spec` is `module.function` (resolved through the file's imports) or a bare name (any call
    /// whose last name it is: `relaunch()`, `agent.relaunch()`, `presence.check()`).
    #[must_use]
    pub fn matches(&self, spec: &str) -> bool {
        if spec.contains('.') {
            self.qualified.as_deref() == Some(spec)
        } else {
            self.last == spec
        }
    }
}

/// A call as written, before the file's import bindings resolve it.
struct Raw {
    offset: usize,
    last: String,
    /// `Some("a.b")` for `a.b.f()`, `Some("g")` for a bare `g()`.
    path: Option<String>,
    bare: bool,
}

#[derive(Default)]
struct Collect {
    calls: Vec<Raw>,
    /// `import m as a` / `import m`: binding -> module.
    modules: BTreeMap<String, String>,
    /// `from m import f as g`: binding -> `m.f`.
    functions: BTreeMap<String, String>,
}

fn dotted(expr: &Expr) -> Option<String> {
    match expr {
        Expr::Name(n) => Some(n.id.to_string()),
        Expr::Attribute(a) => dotted(&a.value).map(|base| format!("{base}.{}", a.attr)),
        _ => None,
    }
}

impl<'a> Visitor<'a> for Collect {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Import(import) => {
                for alias in &import.names {
                    let module = alias.name.to_string();
                    if let Some(asname) = &alias.asname {
                        self.modules.insert(asname.to_string(), module);
                    } else {
                        // `import a.b` binds `a`; `a.b.f()` then resolves through it.
                        let head = module.split('.').next().unwrap_or(&module).to_owned();
                        self.modules.insert(head.clone(), head);
                    }
                }
            }
            Stmt::ImportFrom(from) if from.level == 0 => {
                if let Some(module) = &from.module {
                    for alias in &from.names {
                        let bound = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                        self.functions
                            .insert(bound, format!("{module}.{}", alias.name));
                    }
                }
            }
            _ => {}
        }
        visitor::walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Call(call) = expr {
            let offset = usize::from(expr.start());
            let raw = match call.func.as_ref() {
                Expr::Name(n) => Some(Raw {
                    offset,
                    last: n.id.to_string(),
                    path: Some(n.id.to_string()),
                    bare: true,
                }),
                Expr::Attribute(a) => Some(Raw {
                    offset,
                    last: a.attr.to_string(),
                    path: dotted(&a.value),
                    bare: false,
                }),
                _ => None,
            };
            self.calls.extend(raw);
        }
        visitor::walk_expr(self, expr);
    }
}

impl Collect {
    fn resolve(&self, raw: &Raw) -> Option<String> {
        let path = raw.path.as_deref()?;
        if raw.bare {
            return self.functions.get(path).cloned();
        }
        let (head, rest) = path.split_once('.').map_or((path, ""), |(h, r)| (h, r));
        let module = self.modules.get(head)?;
        let base = if rest.is_empty() {
            module.clone()
        } else {
            format!("{module}.{rest}")
        };
        Some(format!("{base}.{}", raw.last))
    }
}

/// Every call `source` makes, in source order.
///
/// # Errors
///
/// The parser's message when `source` is not Python it can read: a file the check cannot judge
/// is named, never passed.
pub fn calls(source: &str) -> Result<Vec<Site>, String> {
    let parsed = ruff_python_parser::parse_module(source).map_err(|e| e.to_string())?;
    let mut collect = Collect::default();
    collect.visit_body(&parsed.syntax().body);
    let starts: Vec<usize> = std::iter::once(0)
        .chain(source.match_indices('\n').map(|(i, _)| i + 1))
        .collect();
    let line = |offset: usize| starts.partition_point(|&s| s <= offset);
    Ok(collect
        .calls
        .iter()
        .map(|raw| Site {
            line: line(raw.offset),
            last: raw.last.clone(),
            qualified: collect.resolve(raw),
        })
        .collect())
}
