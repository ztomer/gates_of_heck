//! The words of a git invocation and what they do to the index (GOH.GITKILLNATIVE).
//!
//! Read from a Python argv literal or a shell line: which verb runs, whether it writes, and whether
//! it reads the work tree and so takes `index.lock` optionally. `crate::gitdeadline` judges with it; its
//! shapes are pinned there (`a_diff_that_reads_no_work_tree_is_not_a_reader`).
//!
//! A word, not a substring: `git commit -m "fix the diff"` runs no `diff`, `git diff-tree` is not
//! `git diff`, and `git diff "$A" HEAD` compares two revisions, not the work tree (`koffee_big`
//! `tools/cut_release.sh:274`, the false positive that found the class).

/// The verbs that write the index or the refs: killed with SIGKILL, they strand a lock whatever the env.
pub const WRITERS: &[&str] = &[
    "commit",
    "add",
    "merge",
    "rebase",
    "checkout",
    "switch",
    "reset",
    "stash",
    "pull",
    "am",
    "cherry-pick",
    "revert",
    "rm",
    "mv",
    "restore",
    "apply",
    "update-index",
    "worktree",
    "gc",
];
/// How a lock-free verb's words keep it lock-free.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockFree {
    /// Every form of the verb reads: it takes no lock and runs no hook.
    Always,
    /// `git config` reads only with a read flag (`--get`, `--list`, ...) or the `get`/`list`
    /// subcommand; `git config KEY VALUE` writes the config file under `config.lock`.
    ConfigRead,
}

/// The verbs that take no repository lock and run no hook (GOH.GITKILLLOCKFREE).
///
/// Stopped at a deadline, alone or not, they strand nothing, so a bare `timeout` over them is no finding. One
/// row per verb, each pinned by `tests::every_lock_free_row_is_judged_by_its_rule`. `status` and a
/// work-tree `diff` are absent: they take `index.lock` optionally (`takes_optional_lock`).
pub const LOCK_FREE: &[(&str, LockFree)] = &[
    ("ls-remote", LockFree::Always),
    ("rev-parse", LockFree::Always),
    ("cat-file", LockFree::Always),
    ("show-ref", LockFree::Always),
    ("rev-list", LockFree::Always),
    ("for-each-ref", LockFree::Always),
    ("ls-tree", LockFree::Always),
    ("ls-files", LockFree::Always),
    ("merge-base", LockFree::Always),
    ("describe", LockFree::Always),
    ("diff-tree", LockFree::Always),
    ("var", LockFree::Always),
    ("version", LockFree::Always),
    ("config", LockFree::ConfigRead),
];

/// `git config`'s read flags and read subcommands.
const CONFIG_READ_FLAGS: &[&str] = &[
    "--get",
    "--get-all",
    "--get-regexp",
    "--get-urlmatch",
    "--get-color",
    "--get-colorbool",
    "--list",
    "-l",
];

const fn lit(w: &Word) -> Option<&str> {
    match w {
        Word::Lit(s) => Some(s.as_str()),
        _ => None,
    }
}

/// Whether one invocation takes no lock and runs no hook, whatever stops it (`LOCK_FREE`).
#[must_use]
pub fn lock_free(inv: &Invocation) -> bool {
    let Some(verb) = inv.verb.as_deref() else {
        return false;
    };
    match LOCK_FREE.iter().find(|(v, _)| *v == verb).map(|(_, r)| *r) {
        Some(LockFree::Always) => true,
        Some(LockFree::ConfigRead) => {
            matches!(inv.args.first().and_then(lit), Some("get" | "list"))
                || inv
                    .args
                    .iter()
                    .filter_map(lit)
                    .any(|s| CONFIG_READ_FLAGS.contains(&s))
        }
        None => false,
    }
}

/// git's global options that take their value as the next word.
pub const GLOBAL_WITH_VALUE: &[&str] = &["-C", "-c", "--git-dir", "--work-tree", "--namespace"];

/// One word of a git argv: a literal, an expression (a name, a call), or a splat (`*args`),
/// whose words are unknown.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Word {
    /// A literal word.
    Lit(String),
    /// An expression: a name, a call.
    Expr,
    /// A splat (`*args`): any number of unknown words.
    Splat,
}

/// What one git invocation is: its verb (`None` when it is not a literal) and the words after it.
pub struct Invocation {
    /// The verb, when it is a literal.
    pub verb: Option<String>,
    /// The words after the verb.
    pub args: Vec<Word>,
    /// `--no-optional-locks` among the global options.
    pub no_lock: bool,
}

/// Splits the words after `git` into its global options, its verb and the verb's words.
#[must_use]
pub fn invocation(after_git: &[Word]) -> Invocation {
    let mut no_lock = false;
    let mut i = 0;
    while let Some(w) = after_git.get(i) {
        match w {
            Word::Lit(s) if GLOBAL_WITH_VALUE.contains(&s.as_str()) => i += 2,
            Word::Lit(s) if s.starts_with('-') => {
                no_lock |= s == "--no-optional-locks";
                i += 1;
            }
            _ => break,
        }
    }
    let verb = match after_git.get(i) {
        Some(Word::Lit(s)) => Some(s.clone()),
        _ => None,
    };
    let args = after_git
        .get(i + 1..)
        .map_or_else(Vec::new, <[Word]>::to_vec);
    Invocation {
        verb,
        args,
        no_lock,
    }
}

/// Whether `git diff ARGS` compares the WORK TREE.
///
/// That is the one form that refreshes the index and so takes `index.lock` optionally. Not: `--cached`/`--staged` (the index against a commit),
/// `--no-index` (two paths, no repository index), a range (`A..B`, `A...B`), or two operands
/// before `--` (two revisions or two blobs; without `--` git reads two operands that both resolve
/// as two revisions, and a script's `git diff "$A" HEAD` is that form). A splat's words are
/// unknown, so it may be the work tree.
#[must_use]
pub fn diff_reads_work_tree(args: &[Word]) -> bool {
    let mut operands = 0;
    for w in args {
        match w {
            Word::Lit(s) if s == "--" => break,
            Word::Lit(s) if matches!(s.as_str(), "--cached" | "--staged" | "--no-index") => {
                return false;
            }
            Word::Lit(s) if s.starts_with('-') => {}
            Word::Lit(s) if s.contains("..") => return false,
            Word::Lit(_) | Word::Expr => operands += 1,
            Word::Splat => return true,
        }
    }
    operands < 2
}

/// Whether one invocation reads the work tree with the optional lock.
///
/// That is `git status`, or a `git diff` of the work tree (`diff_reads_work_tree`), without `--no-optional-locks`. The plumbing
/// `diff-files`/`diff-index`/`diff-tree` refresh nothing.
#[must_use]
pub fn takes_optional_lock(inv: &Invocation) -> bool {
    !inv.no_lock
        && match inv.verb.as_deref() {
            Some("status") => true,
            Some("diff") => diff_reads_work_tree(&inv.args),
            _ => false,
        }
}

/// The words of a Python argv literal's elements, from the text just after its opening bracket.
#[must_use]
pub fn python_argv(rest: &str) -> Vec<Word> {
    let mut words = Vec::new();
    let (mut depth, mut quote, mut cur) = (0usize, None::<char>, String::new());
    let push = |cur: &mut String, words: &mut Vec<Word>| {
        let e = cur.trim();
        if !e.is_empty() {
            let unprefixed = e.trim_start_matches(['r', 'R', 'u', 'U', 'b', 'B', 'f', 'F']);
            let lit = unprefixed
                .strip_prefix('"')
                .and_then(|s| s.strip_suffix('"'))
                .or_else(|| {
                    unprefixed
                        .strip_prefix('\'')
                        .and_then(|s| s.strip_suffix('\''))
                });
            words.push(match lit {
                Some(s) if !e.starts_with(['f', 'F']) => Word::Lit(s.to_string()),
                _ if e.starts_with('*') => Word::Splat,
                _ => Word::Expr,
            });
        }
        cur.clear();
    };
    for c in rest.chars() {
        if let Some(q) = quote {
            cur.push(c);
            if c == q {
                quote = None;
            }
            continue;
        }
        match c {
            '"' | '\'' => {
                quote = Some(c);
                cur.push(c);
            }
            '[' | '(' | '{' => {
                depth += 1;
                cur.push(c);
            }
            ']' | ')' | '}' if depth == 0 => break,
            ']' | ')' | '}' => {
                depth -= 1;
                cur.push(c);
            }
            ',' if depth == 0 => push(&mut cur, &mut words),
            _ => cur.push(c),
        }
    }
    push(&mut cur, &mut words);
    words
}

/// The words of one shell line.
///
/// Quotes group (and are dropped), `$(` opens a command even inside
/// double quotes, and a pipe, `;`, `&`, a paren, a backtick, a redirection or the close of a `$(`
/// each end a command (`None`).
#[must_use]
pub fn shell_words(code: &str) -> Vec<Option<String>> {
    let mut out: Vec<Option<String>> = Vec::new();
    let (mut cur, mut has) = (String::new(), false);
    let (mut single, mut double) = (false, false);
    let mut resume: Vec<bool> = Vec::new(); // per open `$(`: was it inside double quotes
    let flush = |cur: &mut String, has: &mut bool, out: &mut Vec<Option<String>>| {
        if *has {
            out.push(Some(std::mem::take(cur)));
        }
        *has = false;
    };
    let chars: Vec<char> = code.chars().collect();
    let mut i = 0;
    while let Some(&c) = chars.get(i) {
        i += 1;
        if single {
            if c == '\'' {
                single = false;
            } else {
                cur.push(c);
            }
            continue;
        }
        if c == '$' && chars.get(i) == Some(&'(') {
            i += 1;
            flush(&mut cur, &mut has, &mut out);
            out.push(None);
            resume.push(double);
            double = false;
            continue;
        }
        if double {
            match c {
                '"' => double = false,
                '\\' => {
                    if let Some(&n) = chars.get(i) {
                        cur.push(n);
                        i += 1;
                    }
                }
                _ => cur.push(c),
            }
            continue;
        }
        match c {
            '\'' => (single, has) = (true, true),
            '"' => (double, has) = (true, true),
            '\\' => {
                if let Some(&n) = chars.get(i) {
                    cur.push(n);
                    has = true;
                    i += 1;
                }
            }
            ')' if !resume.is_empty() => {
                flush(&mut cur, &mut has, &mut out);
                out.push(None);
                double = resume.pop().unwrap_or(false);
            }
            '|' | ';' | '&' | '(' | ')' | '`' | '<' | '>' => {
                flush(&mut cur, &mut has, &mut out);
                out.push(None);
            }
            c if c.is_whitespace() => flush(&mut cur, &mut has, &mut out),
            _ => {
                cur.push(c);
                has = true;
            }
        }
    }
    flush(&mut cur, &mut has, &mut out);
    out
}

/// Every git invocation on one shell line: the words from each `git` to the end of its command.
#[must_use]
pub fn shell_invocations(code: &str) -> Vec<Invocation> {
    let words = shell_words(code);
    let mut out = Vec::new();
    let mut at_start = true; // a command's first word, after any VAR=value
    for (i, w) in words.iter().enumerate() {
        let Some(w) = w else {
            at_start = true;
            continue;
        };
        let is_git = w == "git" || w.ends_with("/git");
        if at_start && is_git {
            let rest: Vec<Word> = words[i + 1..]
                .iter()
                .map_while(|w| w.as_ref().map(|s| Word::Lit(s.clone())))
                .collect();
            out.push(invocation(&rest));
        }
        // a command's first word may follow assignments and wrappers (`env`, `command`, `if`)
        let lead = w.contains('=') && !w.starts_with('-')
            || matches!(
                w.as_str(),
                "env"
                    | "command"
                    | "exec"
                    | "if"
                    | "then"
                    | "else"
                    | "elif"
                    | "while"
                    | "until"
                    | "do"
                    | "!"
                    | "time"
                    | "nice"
                    | "nohup"
                    | "sudo"
            );
        at_start = at_start && lead;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shell(line: &str) -> Invocation {
        let words: Vec<Word> = shell_words(line)
            .into_iter()
            .map_while(|w| w.map(Word::Lit))
            .collect();
        invocation(&words)
    }

    /// One row per `LOCK_FREE` verb: its read form is lock-free; a config write is not.
    #[test]
    fn every_lock_free_row_is_judged_by_its_rule() {
        for (verb, rule) in LOCK_FREE {
            let read = match rule {
                LockFree::Always => format!("-C r {verb} x"),
                LockFree::ConfigRead => format!("-C r {verb} --get x"),
            };
            assert!(lock_free(&shell(&read)), "{read}");
            if *rule == LockFree::ConfigRead {
                for write in [format!("{verb} k v"), format!("{verb} --unset k")] {
                    assert!(!lock_free(&shell(&write)), "{write}");
                }
                assert!(lock_free(&shell(&format!("{verb} list"))));
                assert!(lock_free(&shell(&format!("{verb} -l"))));
            }
        }
        assert_eq!(LOCK_FREE.len(), 14, "a row added or dropped: pin it here");
    }

    #[test]
    fn a_writer_or_an_unknown_verb_is_never_lock_free() {
        for w in WRITERS {
            assert!(!LOCK_FREE.iter().any(|(v, _)| v == w), "{w}");
            assert!(!lock_free(&shell(w)), "{w}");
        }
        for v in ["status", "diff", "fetch", "push", "symbolic-ref", ""] {
            assert!(!lock_free(&shell(v)), "{v}");
        }
        let unknown = Invocation {
            verb: None,
            args: vec![],
            no_lock: false,
        };
        assert!(!lock_free(&unknown));
    }
}
