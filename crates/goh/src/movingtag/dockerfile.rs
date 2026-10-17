//! The images a Dockerfile (or Containerfile) pulls, read instruction by instruction.
//!
//! `FROM [--flag=...] <image> [AS <stage>]` and `COPY --from=<image>`, with a backslash-continued
//! instruction read as one and reported at its first line. A `FROM` or `--from=` naming an
//! earlier stage (or a stage index) is not a pull, and `scratch` is no image at all. `ARG` values
//! are substituted as Docker resolves them -- `ARG BASE=node:latest` then `FROM $BASE` pulls
//! `node:latest` -- and an `ARG` with no default leaves the reference unreadable rather than
//! guessed. Not read: `RUN --mount=from=...`, and the `# escape=` directive (the escape is `\`).

use std::collections::{BTreeMap, BTreeSet};

use super::reference::substitute;
use super::scan::Ref;

/// Whether `rel` names a Dockerfile or Containerfile: `Dockerfile`, `Dockerfile.<x>`,
/// `<x>.Dockerfile`, `<x>.dockerfile`, and the same for `Containerfile`, ignoring case.
#[must_use]
pub fn is_dockerfile(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel).to_ascii_lowercase();
    ["dockerfile", "containerfile"].iter().any(|base| {
        name == *base
            || name
                .strip_prefix(base)
                .is_some_and(|rest| rest.starts_with('.'))
            || name
                .strip_suffix(base)
                .is_some_and(|head| head.ends_with('.') && head.len() > 1)
    })
}

/// One instruction: physical lines joined across trailing backslashes, comment lines skipped
/// (Docker drops a comment line inside a continued instruction).
struct Instruction {
    start: usize,
    end: usize,
    text: String,
}

fn instructions(lines: &[&str]) -> Vec<Instruction> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        if lines[i].trim_start().starts_with('#') || lines[i].trim().is_empty() {
            i += 1;
            continue;
        }
        let start = i;
        let mut text = String::new();
        loop {
            let line = lines[i].trim_end();
            if let Some(cont) = line.strip_suffix('\\') {
                text.push_str(cont);
                text.push(' ');
                i += 1;
                while i < lines.len() && lines[i].trim_start().starts_with('#') {
                    i += 1;
                }
                if i < lines.len() {
                    continue;
                }
                i -= 1;
            } else {
                text.push_str(line);
            }
            break;
        }
        out.push(Instruction {
            start: start + 1,
            end: i + 1,
            text,
        });
        i += 1;
    }
    out
}

fn unquote(v: &str) -> &str {
    let v = v.trim();
    for q in ['"', '\''] {
        if let Some(inner) = v.strip_prefix(q).and_then(|s| s.strip_suffix(q)) {
            return inner;
        }
    }
    v
}

/// `ARG A=1 B="2" C` into `vars`: a default sets the name, a bare name keeps what it had.
fn read_args(args: &str, vars: &mut BTreeMap<String, String>) {
    for word in args.split_whitespace() {
        if let Some((name, value)) = word.split_once('=') {
            let value = substitute(unquote(value), vars);
            vars.insert(name.to_owned(), value);
        }
    }
}

/// Every image the Dockerfile `text` pulls, substituted, in order.
#[must_use]
pub fn refs(text: &str) -> Vec<Ref> {
    let lines: Vec<&str> = text.lines().collect();
    let mut vars: BTreeMap<String, String> = BTreeMap::new();
    let mut stages: BTreeSet<String> = BTreeSet::new();
    let mut out = Vec::new();
    for ins in instructions(&lines) {
        let mut words = ins.text.split_whitespace();
        let Some(op) = words.next() else { continue };
        let rest: Vec<&str> = words.collect();
        if op.eq_ignore_ascii_case("ARG") {
            read_args(&rest.join(" "), &mut vars);
        } else if op.eq_ignore_ascii_case("FROM") {
            let mut args = rest.iter().skip_while(|w| w.starts_with("--"));
            let Some(image) = args.next() else { continue };
            let image = substitute(image, &vars);
            if let (Some(kw), Some(stage)) = (args.next(), args.next()) {
                if kw.eq_ignore_ascii_case("AS") {
                    stages.insert(stage.to_ascii_lowercase());
                }
            }
            if !stages.contains(&image.to_ascii_lowercase()) {
                out.push(Ref {
                    start: ins.start,
                    end: ins.end,
                    raw: image,
                });
            }
        } else if op.eq_ignore_ascii_case("COPY") {
            for w in rest.iter().take_while(|w| w.starts_with("--")) {
                let Some(from) = w.strip_prefix("--from=") else {
                    continue;
                };
                let from = substitute(unquote(from), &vars);
                let lower = from.to_ascii_lowercase();
                if !stages.contains(&lower) && !lower.bytes().all(|b| b.is_ascii_digit()) {
                    out.push(Ref {
                        start: ins.start,
                        end: ins.end,
                        raw: from,
                    });
                }
            }
        }
    }
    out
}
