//! Does the producer on the left of a pipe ever end? (BACKLOG 6.22)
//!
//! The report's fix reads the producer to completion first, `out="$(producer)"`. For a producer
//! that never ends -- `tail -f`, `yes`, `log stream`, `journalctl -f`, `docker logs --follow` --
//! that capture never returns, so following the fix hangs the script forever (`ZeroThunder`'s
//! `tests/run_profile.sh:45`, 2026-10-14). Such a finding prints a bounded wait instead.

use super::scan::{ends_command, unquote, words_from};

/// The simple command whose output the pipe at `pipe` carries: back to the previous command
/// boundary (or an opening `(`), then its words, past leading assignments and wrappers.
#[must_use]
pub fn endless_at(m: &[char], raw: &[char], pipe: usize) -> bool {
    let mut start = pipe;
    while start > 0 && !ends_command(m, start - 1) && m[start - 1] != '(' {
        start -= 1;
    }
    let (words, _) = words_from(m, start);
    let words: Vec<String> = words
        .iter()
        .map(|&(s, e)| unquote(&raw[s..e]))
        .skip_while(|w| {
            let assignment = w.split_once('=').is_some_and(|(k, _)| {
                !k.is_empty() && k.chars().all(|c| c.is_alphanumeric() || c == '_')
            });
            assignment || matches!(w.as_str(), "env" | "command" | "exec" | "sudo" | "nohup")
        })
        .collect();
    let Some((cmd, args)) = words.split_first() else {
        return false;
    };
    endless(cmd.rsplit('/').next().unwrap_or(cmd), args)
}

/// A short-flag cluster (`-fn0`, `-F`) carrying one of `letters` before any numeric value.
fn short_flag(args: &[String], letters: &[char]) -> bool {
    args.iter().any(|a| {
        a.strip_prefix('-')
            .filter(|rest| !rest.starts_with('-'))
            .is_some_and(|rest| {
                rest.chars()
                    .take_while(|c| !c.is_ascii_digit())
                    .any(|c| letters.contains(&c))
            })
    })
}

fn long_flag(args: &[String], name: &str) -> bool {
    args.iter()
        .any(|a| a == name || a.starts_with(&format!("{name}=")))
}

/// The producers known never to end on their own.
#[must_use]
pub fn endless(cmd: &str, args: &[String]) -> bool {
    let follows = long_flag(args, "--follow");
    match cmd {
        "tail" | "gtail" => follows || short_flag(args, &['f', 'F']),
        "yes" => true,
        "log" => args.first().is_some_and(|a| a == "stream"),
        "journalctl" | "docker" | "podman" | "kubectl" | "oc" | "stern" => {
            follows || short_flag(args, &['f'])
        }
        _ => follows,
    }
}

/// The bounded-wait fix, for a producer that never ends.
pub const BOUNDED: &str =
    "the producer never ends, so capturing its output first would wait forever: \
    wait with a deadline instead -- for a file, `deadline=$((SECONDS+N)); until grep -q PAT FILE; \
    do (( SECONDS < deadline )) || exit 1; sleep 1; done`; for a stream, read it in a loop that \
    breaks on the match or the deadline, then kill the producer";
