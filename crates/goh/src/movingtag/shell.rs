//! The images a shell script (or a Makefile's recipes) pulls, read command by command
//! (GOH.MOVINGTAGSH).
//!
//! THE INCIDENT: `media_server`'s `scripts/dev/linux_test.sh` ran `IMAGE=rust:1` through `docker run`
//! and `goh moving-tags`, which read only Dockerfiles and YAML, never saw it (SRV.RUSTTAG).
//!
//! WHAT IS READ, and only this, so a script's ordinary text is never taken for an image:
//!
//!   * an assignment to an image variable -- a name holding `IMAGE` (`IMAGE=`, `BASE_IMAGE=`,
//!     `export IMAGE=`, `local AGENT_IMAGE=`) -- at the START of a command, its value with the
//!     file's own earlier assignments and `${NAME:-default}` defaults substituted;
//!   * the image argument of `docker`/`podman` `run`, `create` and `pull` (and `container run`,
//!     `image pull`), at the start of a command: the first word after the options. The flag grammar
//!     is the CLI's: a known boolean flag takes no value, `--name=value` carries its own, and any
//!     other long flag takes the next word. A short flag this reader does not know leaves the
//!     reference unreadable rather than guessed, so a command word is never judged as an image.
//!
//! A word is read through the shell's quoting: `grep -q '^docker run$'` is one quoted word, not a
//! command; a backslash-continued command is one command, reported from its first line to the
//! image's; a comment and a heredoc's body are not commands.
//!
//! WHAT IS NOT A PULL: a name the same script BUILDS (`docker build --tag "$IMAGE"`, `docker
//! commit CTR "$IMAGE"`, `docker tag SRC "$IMAGE"`) is its own output, so its assignment and the
//! runs of it are judged and found to pull nothing. An image argument that is exactly an image
//! variable (`"$IMAGE"`) is judged where the variable is assigned, once, not again at each run.
//!
//! A Makefile is read the same way: a recipe line is a shell command, and `NAME ?= value` (any of
//! make's assignment operators) is an assignment; `$(NAME)` is `${NAME}` and `$$` is `$`.

use std::collections::{BTreeMap, BTreeSet};

use super::reference::substitute;
use super::scan::Ref;
use super::shlex::{commands, Word};

/// What a reference carries when the flag grammar could not be read: unreadable, never guessed.
const UNREAD: &str = "{{ an option this reader does not know }}";

/// Whether `rel` names a Makefile: `Makefile`, `GNUmakefile`, `makefile`, `*.mk`.
#[must_use]
pub fn is_makefile(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel).to_ascii_lowercase();
    name == "makefile"
        || name == "gnumakefile"
        || std::path::Path::new(&name)
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("mk"))
}

/// `NAME=value`, when the word is an assignment: an unquoted identifier, then `=`.
fn assignment(w: &Word) -> Option<(&str, &str)> {
    if w.quoted {
        return None;
    }
    let (name, value) = w.text.split_once('=')?;
    let mut b = name.bytes();
    (b.next()
        .is_some_and(|f| f.is_ascii_alphabetic() || f == b'_')
        && b.all(|x| x.is_ascii_alphanumeric() || x == b'_'))
    .then_some((name, value))
}

/// A variable that holds an image by its name: `IMAGE`, `BASE_IMAGE`, `IMAGE_TAG`.
fn is_image_var(name: &str) -> bool {
    name.contains("IMAGE")
        && name
            .bytes()
            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit() || b == b'_')
}

/// Words that start a command before the command itself.
const PREFIX: [&str; 14] = [
    "if", "then", "else", "elif", "do", "while", "until", "!", "time", "{", "exec", "command",
    "nohup", "sudo",
];
const DECLARE: [&str; 5] = ["export", "local", "readonly", "declare", "typeset"];

/// The command's leading assignments, and the words of the command after them.
fn split(cmd: &[Word]) -> (Vec<&Word>, &[Word]) {
    let mut i = 0;
    let mut assigns = Vec::new();
    while i < cmd.len() {
        let w = &cmd[i];
        if assignment(w).is_some() {
            assigns.push(w);
        } else if PREFIX.contains(&w.text.as_str()) || w.text == "env" {
            i += 1;
            while cmd.get(i).is_some_and(|x| x.text.starts_with('-')) {
                i += 1;
            }
            continue;
        } else if DECLARE.contains(&w.text.as_str()) {
            assigns.extend(cmd[i + 1..].iter().filter(|x| assignment(x).is_some()));
            return (assigns, &[]);
        } else {
            break;
        }
        i += 1;
    }
    (assigns, &cmd[i..])
}

/// Boolean flags of `docker`/`podman` `run`, `create` and `pull`: each takes no value.
const BOOL_LONG: [&str; 18] = [
    "--rm",
    "--detach",
    "--interactive",
    "--tty",
    "--init",
    "--privileged",
    "--read-only",
    "--oom-kill-disable",
    "--no-healthcheck",
    "--publish-all",
    "--quiet",
    "--disable-content-trust",
    "--all-tags",
    "--replace",
    "--rmi",
    "--env-host",
    "--read-only-tmpfs",
    "--no-hosts",
];
const BOOL_SHORT: &str = "ditPq";
const VALUE_SHORT: &str = "aevwpumlhc";

/// Whether a command word runs docker or podman: by name or path, or through a variable named for
/// one (`"$docker"`, `"$DOCKER"`, `$PODMAN_BIN`).
fn is_docker(word: &str) -> bool {
    let named = |s: &str| {
        let s = s.to_ascii_lowercase();
        s.contains("docker") || s.contains("podman")
    };
    bare_var(word).map_or_else(
        || matches!(word.rsplit('/').next(), Some("docker" | "podman")),
        named,
    )
}

/// The verb a `docker`/`podman` command runs (`run`, `pull`, ...) and the words after it.
fn docker_verb(rest: &[Word]) -> Option<(&str, &[Word])> {
    if !is_docker(&rest.first()?.text) {
        return None;
    }
    let verb = rest.get(1)?.text.as_str();
    if matches!(verb, "container" | "image" | "buildx") {
        return Some((rest.get(2)?.text.as_str(), rest.get(3..)?));
    }
    Some((verb, rest.get(2..)?))
}

/// The first positional word after the options, or `Err` when an option could not be read.
fn positionals(args: &[Word]) -> Result<Vec<&Word>, ()> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < args.len() {
        let t = args[i].text.as_str();
        if !out.is_empty() || args[i].quoted || !t.starts_with('-') || t == "-" {
            out.push(&args[i]);
        } else if t == "--" {
            out.extend(args[i + 1..].iter());
            break;
        } else if t.starts_with("--") {
            let skip = !t.contains('=') && !BOOL_LONG.contains(&t);
            i += usize::from(skip);
        } else {
            let flags: Vec<char> = t[1..].chars().collect();
            if !flags.iter().all(|f| BOOL_SHORT.contains(*f)) {
                if !VALUE_SHORT.contains(flags[0]) {
                    return Err(());
                }
                i += usize::from(flags.len() == 1);
            }
        }
        i += 1;
    }
    Ok(out)
}

/// `text` substituted until nothing more resolves (a default may name another variable).
fn resolve(text: &str, vars: &BTreeMap<String, String>) -> String {
    let mut s = text.to_owned();
    for _ in 0..4 {
        let next = substitute(&s, vars);
        if next == s {
            break;
        }
        s = next;
    }
    s
}

/// The variable a word is exactly (`$IMAGE`, `${IMAGE}`), if it is one.
fn bare_var(text: &str) -> Option<&str> {
    let name = text
        .strip_prefix("${")
        .and_then(|r| r.strip_suffix('}'))
        .or_else(|| text.strip_prefix('$'))?;
    (!name.is_empty() && name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'))
        .then_some(name)
}

/// The variables a word names.
fn vars_in(text: &str) -> impl Iterator<Item = String> + '_ {
    text.split('$').skip(1).map(|r| {
        r.trim_start_matches('{')
            .bytes()
            .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
            .map(char::from)
            .collect()
    })
}

/// A Makefile line that is not a recipe: `NAME = value` (any of make's operators) as the shell's
/// `NAME='value'`, anything else blank.
fn make_assignment(line: &str) -> String {
    let body = line
        .trim_start()
        .strip_prefix("export ")
        .unwrap_or(line)
        .trim_start();
    let name_len = body
        .bytes()
        .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
        .count();
    let (name, rest) = body.split_at(name_len);
    let rest = rest.trim_start();
    let value = ["::=", ":=", "?=", "+=", "!=", "="]
        .iter()
        .find_map(|op| rest.strip_prefix(op));
    match value {
        Some(v) if !name.is_empty() && !v.contains('\'') => format!("{name}='{}'", v.trim()),
        _ => String::new(),
    }
}

/// A Makefile as shell text, line for line: a recipe line is its command, an assignment is
/// `NAME='value'`, anything else is blank.
fn from_make(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for line in text.lines() {
        let shell = line.strip_prefix('\t').map_or_else(
            || make_assignment(line),
            |recipe| recipe.trim_start_matches(['@', '-', '+']).to_owned(),
        );
        out.push_str(&make_vars(&shell).replace("$$", "$"));
        out.push('\n');
    }
    out
}

/// Make's `$(NAME)` as the shell's `${NAME}`; any other `$(` (a function, a subshell) as written.
fn make_vars(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(at) = rest.find("$(") {
        let (head, tail) = rest.split_at(at);
        out.push_str(head);
        let body = &tail[2..];
        let n = body
            .bytes()
            .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_')
            .count();
        if n > 0 && body.as_bytes().get(n) == Some(&b')') && !head.ends_with('$') {
            out.push_str("${");
            out.push_str(&body[..n]);
            out.push('}');
            rest = &body[n + 1..];
        } else {
            out.push_str("$(");
            rest = body;
        }
    }
    out.push_str(rest);
    out
}

/// Every image the shell script (or, `make`, the Makefile) `text` pulls, in order.
#[must_use]
pub fn refs(text: &str, make: bool) -> Vec<Ref> {
    let owned;
    let text = if make {
        owned = from_make(text);
        owned.as_str()
    } else {
        text
    };
    let cmds = commands(text);
    let mut vars: BTreeMap<String, String> = BTreeMap::new();
    let (mut built_vars, mut built) = (BTreeSet::new(), BTreeSet::new());
    for cmd in &cmds {
        let (assigns, rest) = split(cmd);
        for w in assigns {
            if let Some((name, value)) = assignment(w) {
                let v = resolve(value, &vars);
                vars.insert(name.to_owned(), v);
            }
        }
        let outputs: Vec<&Word> = match docker_verb(rest) {
            Some(("build", args)) => args
                .windows(2)
                .filter(|p| p[0].text == "-t" || p[0].text == "--tag")
                .map(|p| &p[1])
                .chain(args.iter().filter(|w| w.text.starts_with("--tag=")))
                .collect(),
            Some(("commit" | "tag", args)) => positionals(args)
                .ok()
                .and_then(|p| p.get(1).copied())
                .into_iter()
                .collect(),
            _ => Vec::new(),
        };
        for w in outputs {
            let t = w.text.trim_start_matches("--tag=");
            built_vars.extend(vars_in(t));
            built.insert(resolve(t, &vars));
        }
    }
    let is_built =
        |r: &str| built.contains(r) || built.iter().any(|b| b.split(':').next() == Some(r));
    let mut out = Vec::new();
    for cmd in &cmds {
        let (assigns, rest) = split(cmd);
        for w in assigns {
            let Some((name, value)) = assignment(w).filter(|(n, _)| is_image_var(n)) else {
                continue;
            };
            let raw = resolve(value, &vars);
            // A path is a file, never a registry reference (`FLASH_IMAGE=/sbin/flash_image`).
            let path = raw.starts_with(['/', '.', '~']);
            let raw = if path || built_vars.contains(name) || is_built(&raw) {
                String::new()
            } else {
                raw
            };
            out.push(Ref {
                start: w.line,
                end: w.line,
                raw,
            });
        }
        let Some(("run" | "create" | "pull", args)) = docker_verb(rest) else {
            continue;
        };
        let image = match positionals(args) {
            Err(()) => Some((
                args.last().map_or(rest[0].line, |w| w.line),
                UNREAD.to_owned(),
            )),
            Ok(p) => p.first().map(|w| {
                let raw = match bare_var(&w.text) {
                    Some(v) if is_image_var(v) && vars.contains_key(v) => String::new(),
                    _ => resolve(&w.text, &vars),
                };
                (w.line, if is_built(&raw) { String::new() } else { raw })
            }),
        };
        if let Some((end, raw)) = image {
            out.push(Ref {
                start: rest[0].line,
                end,
                raw,
            });
        }
    }
    out
}

#[cfg(test)]
#[path = "shell_tests.rs"]
mod tests;
