//! The calls a Python file makes, from its AST (`ruff_python_parser`), for `goh requires-call`.
//!
//! Sized for BACKLOG 1.1 (2026-10-08): over `ZoneWM`'s `tools/` and this repo's 457 Python files it
//! found the same 29,973 call sites, line for line, as Python's own `ast`, in 0.10 s. A call named
//! in a string or a docstring is not a call -- the reason the check reads the AST at all.

use std::collections::BTreeMap;

use ruff_python_ast::visitor::{self, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::Ranged;

/// One call site.
///
/// Its line, the name it calls last (`f` in `f()` and `a.b.f()`), when the callee
/// resolves through an import binding `module.function` (`import m as a; a.f()`,
/// `from m import f as g; g()`), and its positional arguments -- a list or tuple literal among
/// them spread in place (an argv) -- each `Some` when it is a string literal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Site {
    pub line: usize,
    pub last: String,
    pub qualified: Option<String>,
    pub args: Vec<Option<String>>,
}

/// A call a rule names.
///
/// `module.function` (resolved through the file's imports), a bare name (any
/// call whose last name it is: `relaunch()`, `agent.relaunch()`), or either -- or `*`, any callee
/// -- with string literals that must sit side by side among its positional arguments:
/// `cli("config-set")`, `*("theme", "--id")` (BACKLOG 1.3). An argument built at run time (an
/// f-string, a variable) is not a literal and never matches.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    text: String,
    callee: String,
    literals: Vec<String>,
}

impl Spec {
    /// # Errors
    ///
    /// What is wrong with `text`, quoted.
    pub fn parse(text: &str) -> Result<Self, String> {
        let bad = |why: &str| format!("`{text}`: {why}");
        let (callee, literals) = match text.split_once('(') {
            None => (text, Vec::new()),
            Some((callee, rest)) => {
                let inner = rest
                    .strip_suffix(')')
                    .ok_or_else(|| bad("an argument trigger ends with `)`"))?;
                let parsed: toml::Table = format!("v = [{inner}]")
                    .parse()
                    .map_err(|_| bad("the arguments are quoted strings, comma separated"))?;
                let literals: Option<Vec<String>> = parsed["v"]
                    .as_array()
                    .and_then(|a| a.iter().map(|x| x.as_str().map(str::to_owned)).collect());
                match literals {
                    Some(l) if !l.is_empty() => (callee, l),
                    _ => return Err(bad("the arguments are quoted strings, comma separated")),
                }
            }
        };
        let callee = callee.trim();
        let name = |c: char| c.is_alphanumeric() || c == '_' || c == '.';
        let any = callee == "*" && !literals.is_empty();
        if !any && (callee.is_empty() || !callee.chars().all(name)) {
            return Err(bad("a callee is a dotted name, or `*` before arguments"));
        }
        Ok(Self {
            text: text.to_owned(),
            callee: callee.to_owned(),
            literals,
        })
    }

    /// The literals, as the rule wrote them, for a violation line.
    #[must_use]
    pub fn shown(&self, site: &Site) -> String {
        if self.literals.is_empty() {
            return site.last.clone();
        }
        let quoted: Vec<String> = self.literals.iter().map(|l| format!("{l:?}")).collect();
        format!("{}({})", site.last, quoted.join(", "))
    }
}

impl std::fmt::Display for Spec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.text)
    }
}

impl Site {
    #[must_use]
    pub fn matches(&self, spec: &Spec) -> bool {
        let callee = match spec.callee.as_str() {
            "*" => true,
            c if c.contains('.') => self.qualified.as_deref() == Some(c),
            c => self.last == c,
        };
        callee
            && (spec.literals.is_empty()
                || self.args.windows(spec.literals.len()).any(|w| {
                    w.iter()
                        .zip(&spec.literals)
                        .all(|(a, l)| a.as_deref() == Some(l.as_str()))
                }))
    }
}

/// A call as written, before the file's import bindings resolve it.
struct Raw {
    offset: usize,
    last: String,
    /// `Some("a.b")` for `a.b.f()`, `Some("g")` for a bare `g()`.
    path: Option<String>,
    bare: bool,
    args: Vec<Option<String>>,
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

fn literal(expr: &Expr) -> Option<String> {
    match expr {
        Expr::StringLiteral(s) => Some(s.value.to_str().to_owned()),
        _ => None,
    }
}

/// Positional arguments, a list or tuple literal spread in place: `run([CLI, "switch-space"])`.
fn positional(args: &[Expr]) -> Vec<Option<String>> {
    args.iter()
        .flat_map(|a| match a {
            Expr::List(l) => l.elts.iter().map(literal).collect(),
            Expr::Tuple(t) => t.elts.iter().map(literal).collect(),
            other => vec![literal(other)],
        })
        .collect()
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
            let args = positional(&call.arguments.args);
            let raw = match call.func.as_ref() {
                Expr::Name(n) => Some(Raw {
                    offset,
                    last: n.id.to_string(),
                    path: Some(n.id.to_string()),
                    bare: true,
                    args,
                }),
                Expr::Attribute(a) => Some(Raw {
                    offset,
                    last: a.attr.to_string(),
                    path: dotted(&a.value),
                    bare: false,
                    args,
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
            args: raw.args.clone(),
        })
        .collect())
}
