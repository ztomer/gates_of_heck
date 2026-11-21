//! The zsh no-word-split trap, read from a real shell parse (`brush-parser`).
//!
//! zsh does not split an unquoted `$VAR` on whitespace (bash does). A command written for bash
//! and run in zsh passes a "list" as ONE word: `F="a b"; git commit -- $F` commits a file named
//! `a b`; `L="env A=1 python x.py"; $L done X` looks for a command named the whole string and
//! runs nothing; `for f in $FILES` loops once, over the whole list (2026-11-21, three times in
//! one day, one of them overwriting a lane's uncommitted edits).
//!
//! A finding is an unquoted plain `$VAR` (or `${VAR}`) used as a command word, as a `for` word
//! list or as an argument, where `VAR` was assigned, earlier in the same command, a value holding
//! whitespace. An array assignment (`F=(a b)`) is the fix, so it clears the name; a later
//! assignment without whitespace clears it too. A prefix assignment (`F="a b" cmd`) sets only
//! `cmd`'s environment, never the shell's, so it is not one. What the parser cannot read is not
//! judged: the guard must never be the thing that breaks an ordinary command.

use std::collections::BTreeMap;

use brush_parser::ast::{
    Assignment, AssignmentName, AssignmentValue, Command, CommandPrefixOrSuffixItem,
    CompoundCommand, CompoundList, SimpleCommand, Word,
};
use brush_parser::word::{self, Parameter, ParameterExpr, WordPiece, WordPieceWithSource};
use brush_parser::{Parser, ParserOptions};

/// Where the unquoted variable stood.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    CommandWord,
    ForList,
    Argument,
}

impl Role {
    const fn says(self) -> &'static str {
        match self {
            Self::CommandWord => "as the command word",
            Self::ForList => "as a `for` word list",
            Self::Argument => "as an argument list",
        }
    }
}

/// One unquoted use of a whitespace-holding variable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    pub var: String,
    pub value: String,
    pub role: Role,
}

/// The builtins whose `NAME=value` arguments assign shell variables.
const DECLARERS: [&str; 6] = [
    "export", "local", "typeset", "declare", "readonly", "integer",
];

/// Every finding in `command`, in source order; empty when it does not parse.
#[must_use]
pub fn findings(command: &str) -> Vec<Finding> {
    let options = ParserOptions::default();
    let mut parser = Parser::new(std::io::BufReader::new(command.as_bytes()), &options);
    let Ok(program) = parser.parse_program() else {
        return Vec::new();
    };
    let mut walk = Walk {
        options,
        spaced: BTreeMap::new(),
        found: Vec::new(),
    };
    for list in &program.complete_commands {
        walk.list(list);
    }
    walk.found
}

struct Walk {
    options: ParserOptions,
    /// Each variable that holds whitespace now, and the value's source text.
    spaced: BTreeMap<String, String>,
    found: Vec<Finding>,
}

impl Walk {
    fn list(&mut self, list: &CompoundList) {
        for item in &list.0 {
            for (_, pipeline) in &item.0 {
                for command in &pipeline.seq {
                    self.command(command);
                }
            }
        }
    }

    fn command(&mut self, command: &Command) {
        match command {
            Command::Simple(simple) => self.simple(simple),
            Command::Compound(compound, _) => self.compound(compound),
            Command::Function(function) => self.compound(&function.body.0),
            Command::ExtendedTest(..) => {}
        }
    }

    fn compound(&mut self, compound: &CompoundCommand) {
        match compound {
            CompoundCommand::BraceGroup(group) => self.list(&group.list),
            CompoundCommand::Subshell(sub) => self.list(&sub.list),
            CompoundCommand::ForClause(clause) => {
                for value in clause.values.iter().flatten() {
                    self.used(value, Role::ForList);
                }
                self.list(&clause.body.list);
            }
            CompoundCommand::ArithmeticForClause(clause) => self.list(&clause.body.list),
            CompoundCommand::CaseClause(clause) => {
                for item in &clause.cases {
                    if let Some(list) = &item.cmd {
                        self.list(list);
                    }
                }
            }
            CompoundCommand::IfClause(clause) => {
                self.list(&clause.condition);
                self.list(&clause.then);
                for branch in clause.elses.iter().flatten() {
                    if let Some(condition) = &branch.condition {
                        self.list(condition);
                    }
                    self.list(&branch.body);
                }
            }
            CompoundCommand::WhileClause(clause) | CompoundCommand::UntilClause(clause) => {
                self.list(&clause.0);
                self.list(&clause.1.list);
            }
            CompoundCommand::Coprocess(co) => self.command(&co.body),
            CompoundCommand::Arithmetic(_) => {}
        }
    }

    fn simple(&mut self, simple: &SimpleCommand) {
        let name = simple.word_or_name.as_ref();
        if let Some(word) = name {
            self.used(word, Role::CommandWord);
        }
        let declares = name.is_some_and(|w| DECLARERS.contains(&w.value.as_str()));
        let mut assigned: Vec<&Assignment> = Vec::new();
        let mut by_builtin: Vec<(String, String)> = Vec::new();
        for item in simple.suffix.iter().flat_map(|s| &s.0) {
            match item {
                CommandPrefixOrSuffixItem::Word(word) if declares => {
                    if let Some((var, value)) = split_assignment(&word.value) {
                        by_builtin.push((var, value));
                    }
                }
                CommandPrefixOrSuffixItem::Word(word) => self.used(word, Role::Argument),
                CommandPrefixOrSuffixItem::AssignmentWord(assignment, _) if declares => {
                    assigned.push(assignment);
                }
                CommandPrefixOrSuffixItem::ProcessSubstitution(_, sub) => self.list(&sub.list),
                _ => {}
            }
        }
        for item in simple.prefix.iter().flat_map(|p| &p.0) {
            match item {
                // `F="a b" cmd` sets cmd's environment only; with no command it sets the shell's.
                CommandPrefixOrSuffixItem::AssignmentWord(assignment, _) if name.is_none() => {
                    assigned.push(assignment);
                }
                _ => {}
            }
        }
        for assignment in assigned {
            self.assign(assignment);
        }
        for (var, value) in by_builtin {
            self.set_scalar(var, &value, false);
        }
    }

    fn assign(&mut self, assignment: &Assignment) {
        let AssignmentName::VariableName(var) = &assignment.name else {
            return;
        };
        match &assignment.value {
            AssignmentValue::Scalar(word) => {
                self.set_scalar(var.clone(), &word.value, assignment.append);
            }
            AssignmentValue::Array(_) => {
                self.spaced.remove(var);
            }
        }
    }

    fn set_scalar(&mut self, var: String, value: &str, append: bool) {
        let holds = word::parse(value, &self.options).is_ok_and(|p| self.holds_space(&p));
        if holds {
            let text = match (append, self.spaced.get(&var)) {
                (true, Some(before)) => format!("{before}{value}"),
                _ => value.to_owned(),
            };
            self.spaced.insert(var, text);
        } else if !append {
            self.spaced.remove(&var);
        }
    }

    /// Whether a value's pieces carry whitespace once expanded.
    fn holds_space(&self, pieces: &[WordPieceWithSource]) -> bool {
        pieces.iter().any(|p| match &p.piece {
            WordPiece::Text(t)
            | WordPiece::SingleQuotedText(t)
            | WordPiece::AnsiCQuotedText(t)
            | WordPiece::EscapeSequence(t) => t.chars().any(char::is_whitespace),
            WordPiece::DoubleQuotedSequence(inner)
            | WordPiece::GettextDoubleQuotedSequence(inner) => self.holds_space(inner),
            WordPiece::ParameterExpansion(expr) => {
                plain_name(expr).is_some_and(|v| self.spaced.contains_key(v))
            }
            _ => false,
        })
    }

    /// A word whose TOP-LEVEL pieces (outside any quotes) expand a whitespace-holding variable.
    fn used(&mut self, word: &Word, role: Role) {
        // The parser already read this word as part of the program, and `word::parse` took every
        // such word in a 400 000-string fuzz, so the empty fallback is never the path taken.
        let pieces = word::parse(&word.value, &self.options).unwrap_or_default();
        for piece in &pieces {
            if let WordPiece::ParameterExpansion(expr) = &piece.piece {
                if let Some(value) = plain_name(expr).and_then(|v| self.spaced.get(v)) {
                    let var = plain_name(expr).unwrap_or_default().to_owned();
                    self.found.push(Finding {
                        var,
                        value: value.clone(),
                        role,
                    });
                }
            }
        }
    }
}

/// `$VAR` / `${VAR}`: the plain expansion zsh leaves whole.
fn plain_name(expr: &ParameterExpr) -> Option<&str> {
    match expr {
        ParameterExpr::Parameter {
            parameter: Parameter::Named(name),
            indirect: false,
        } => Some(name),
        _ => None,
    }
}

/// `NAME=value` as a declaration builtin's argument; `None` for any other word.
///
/// The parser reads an unquoted `NAME=value` as an assignment word, so a plain word reaches here
/// only when the whole assignment is quoted (`export "F=a b"`, which still assigns): the quotes
/// are taken off the name and put back round the value, so the value reads as it was written.
fn split_assignment(text: &str) -> Option<(String, String)> {
    let quote = text.chars().next().filter(|c| matches!(c, '"' | '\''));
    let body = quote.map_or(text, |q| {
        let rest = &text[1..];
        rest.strip_suffix(q).unwrap_or(rest)
    });
    let (var, value) = body.split_once('=')?;
    let mut chars = var.chars();
    let first = chars.next()?;
    let ok = (first.is_ascii_alphabetic() || first == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_');
    let q = quote.map(String::from).unwrap_or_default();
    ok.then(|| (var.to_owned(), format!("{q}{value}{q}")))
}

/// The refusal for the first finding: what zsh does with it, and the three fixes.
#[must_use]
pub fn reason(command: &str, shell: &str) -> Option<String> {
    let first = findings(command).into_iter().next()?;
    let Finding { var, value, role } = first;
    Some(format!(
        "The tool shell is zsh ({shell}), and zsh does not word-split an unquoted ${var}: \
         {var} was assigned {value} (it holds whitespace) and is used {how}, so it is ONE word, \
         not a list -- a command named the whole string, a `for` that loops once, one argument. \
         Write a list as an array (`{var}=(a b); cmd -- \"${{{var}[@]}}\"`, `for f in \
         \"${{{var}[@]}}\"`), a command as a function (`{lower}() {{ env A=1 python x.py \"$@\"; \
         }}; {lower} done X`), or inline the words. The agent tool shell should be bash 5 \
         (`goh shell-check` says what is missing). To run it as written on purpose, add \
         `# zsh-split-ok: <reason>` to the command.",
        how = role.says(),
        lower = var.to_lowercase(),
    ))
}
