//! What the ported checkers asked `ast` for, answered from the token stream:
//! bare-string statements, docstrings, `def` names, module-level string lists.

use super::{Kind, Lexed, Stmt, Tok};

fn strip_parens(toks: &[Tok]) -> &[Tok] {
    let mut t = toks;
    while t.len() >= 2
        && t[0].kind == Kind::Open
        && t[0].text == "("
        && t[t.len() - 1].kind == Kind::Close
        && matching(t, 0) == Some(t.len() - 1)
    {
        t = &t[1..t.len() - 1];
    }
    t
}

fn matching(toks: &[Tok], open: usize) -> Option<usize> {
    let d = toks[open].depth;
    (open + 1..toks.len()).find(|&j| toks[j].kind == Kind::Close && toks[j].depth == d)
}

/// Is this token run one `str` constant (`ast.Constant` holding a `str`)?
fn str_constant(toks: &[Tok]) -> bool {
    let inner = strip_parens(toks);
    !inner.is_empty() && inner.iter().all(Tok::plain_str)
}

impl Lexed {
    fn stmt_toks(&self, s: &Stmt) -> &[Tok] {
        &self.toks[s.toks.clone()]
    }

    /// Line ranges of every statement that is only a `str` literal.
    #[must_use]
    pub fn bare_strings(&self) -> Vec<(usize, usize)> {
        self.stmts.iter().filter_map(|s| self.str_stmt(s)).collect()
    }

    /// Line ranges of the docstrings: the first statement of a module,
    /// `def` or `class` body, when it is only a `str` literal.
    #[must_use]
    pub fn docstrings(&self) -> Vec<(usize, usize)> {
        self.stmts
            .iter()
            .filter(|s| s.doc_slot)
            .filter_map(|s| self.str_stmt(s))
            .collect()
    }

    fn str_stmt(&self, s: &Stmt) -> Option<(usize, usize)> {
        let t = self.stmt_toks(s);
        let only_parens_and_str = t.iter().all(|k| {
            k.plain_str()
                || (matches!(k.kind, Kind::Open | Kind::Close)
                    && matches!(k.text.as_str(), "(" | ")"))
        });
        (only_parens_and_str && t.iter().any(Tok::plain_str))
            .then(|| (t[0].start.0, t[t.len() - 1].end.0))
    }

    /// Every function name (`def NAME`, sync or async, any depth).
    #[must_use]
    pub fn def_names(&self) -> Vec<&str> {
        self.toks
            .windows(2)
            .filter(|w| w[0].kind == Kind::Name && w[0].text == "def" && w[1].kind == Kind::Name)
            .map(|w| w[1].text.as_str())
            .collect()
    }

    /// `{name: element count}` for every module-level `NAME = [..]` /
    /// `NAME = {..}` whose elements (a dict's keys) are all `str` constants;
    /// the first assignment of a name wins, as `dict.setdefault` does.
    #[must_use]
    pub fn declared_lists(&self) -> std::collections::BTreeMap<String, usize> {
        let mut out = std::collections::BTreeMap::new();
        for s in self
            .stmts
            .iter()
            .filter(|s| s.level == 0 && !s.after_header)
        {
            let t = self.stmt_toks(s);
            let cuts: Vec<usize> = (0..t.len())
                .filter(|&j| t[j].depth == 0 && t[j].kind == Kind::Op && t[j].text == "=")
                .collect();
            let Some(&last) = cuts.last() else {
                continue;
            };
            let Some(count) = literal_count(strip_parens(&t[last + 1..])) else {
                continue;
            };
            let mut from = 0usize;
            for &cut in &cuts {
                let target = &t[from..cut];
                if target.len() == 1 && target[0].kind == Kind::Name {
                    out.entry(target[0].text.clone()).or_insert(count);
                }
                from = cut + 1;
            }
        }
        out
    }
}

/// Elements of a list literal, or keys of a dict literal, when every one is a
/// `str` constant and there is at least one; `None` otherwise.
fn literal_count(v: &[Tok]) -> Option<usize> {
    let open = v.first()?;
    if open.kind != Kind::Open
        || !matches!(open.text.as_str(), "[" | "{")
        || matching(v, 0) != Some(v.len() - 1)
    {
        return None;
    }
    let inner = &v[1..v.len() - 1];
    let d = open.depth + 1;
    if inner
        .iter()
        .any(|k| k.depth == d && k.kind == Kind::Name && k.text == "for")
    {
        return None; // a comprehension
    }
    let mut items: Vec<&[Tok]> = inner
        .split(|k| k.depth == d && k.kind == Kind::Op && k.text == ",")
        .collect();
    if items.last().is_some_and(|i| i.is_empty()) {
        items.pop();
    }
    if items.is_empty() || items.iter().any(|i| i.is_empty()) {
        return None;
    }
    if open.text == "[" {
        return items.iter().all(|i| str_constant(i)).then_some(items.len());
    }
    let colon = |i: &[Tok]| {
        i.iter()
            .position(|k| k.depth == d && k.kind == Kind::Op && k.text == ":")
    };
    if !items.iter().any(|i| colon(i).is_some()) {
        return None; // a set
    }
    items
        .iter()
        .all(|i| colon(i).is_some_and(|c| str_constant(&i[..c])))
        .then_some(items.len())
}
