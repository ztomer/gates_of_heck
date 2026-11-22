//! The agent tool shell (GOH.ZSHSPLITGUARD, owner 2026-11-21: "Yes, both").
//!
//! Two commands, wired as Claude Code hooks by `hooks/claude/registrations.json`:
//!
//! * `goh shell-check` (`SessionStart`): the tool shell is bash >= 5 and its `BASH_ENV` builds the
//!   house env (`check`); exit 1 naming each fix otherwise. It also probes the harness grep's
//!   `-v -q` exit code and records it for the Bash guard (`grep_probe`, GOH.UGREPVQ).
//! * `goh zsh-split-guard` (`PreToolUse` on the shell tools): while the tool shell is still zsh,
//!   refuse an unquoted `$VAR` used as a command word, a `for` list or an argument list when the
//!   command assigned VAR a whitespace-holding value (`split`). Under any other shell it passes
//!   everything: removing the cause (bash) is the fix, the guard covers the gap until it lands.
//!   The escape is `# zsh-split-ok: <reason>`.

pub mod check;
pub mod grep_probe;
pub mod split;

#[cfg(test)]
mod tests;
#[cfg(test)]
mod walk_tests;

use std::io::Read;
use std::path::Path;

use goh_tui::{ERR, OK};

/// The tools whose input holds a shell command (`lib/shell_tools.py`'s list).
pub const SHELL_TOOLS: [&str; 2] = ["Bash", "Monitor"];

/// The shell Claude Code runs tool commands in: `CLAUDE_CODE_SHELL`, else the login `SHELL`.
pub fn tool_shell(env: &dyn Fn(&str) -> Option<String>) -> String {
    env("CLAUDE_CODE_SHELL")
        .filter(|v| !v.is_empty())
        .or_else(|| env("SHELL"))
        .unwrap_or_default()
}

/// A zsh by its file name (`/bin/zsh`, `/opt/homebrew/bin/zsh-5.9`).
#[must_use]
pub fn is_zsh(shell: &str) -> bool {
    Path::new(shell)
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with("zsh"))
}

/// Whether the command carries the reasoned escape.
#[must_use]
pub fn escaped(command: &str) -> bool {
    command.match_indices('#').any(|(at, _)| {
        command[at + 1..]
            .trim_start()
            .strip_prefix("zsh-split-ok:")
            .and_then(|r| r.trim_start_matches([' ', '\t']).chars().next())
            .is_some_and(|c| !c.is_whitespace())
    })
}

/// The refusal for a `PreToolUse` event, or `None` to let it run.
pub fn judge_event(
    event: &serde_json::Value,
    env: &dyn Fn(&str) -> Option<String>,
) -> Option<String> {
    let tool = event.get("tool_name")?.as_str()?;
    if !SHELL_TOOLS.contains(&tool) {
        return None;
    }
    let command = event.get("tool_input")?.get("command")?.as_str()?;
    let shell = tool_shell(env);
    if !is_zsh(&shell) || escaped(command) {
        return None;
    }
    split::reason(command, &shell)
}

/// The `PreToolUse` JSON that denies a tool call with `reason`.
#[must_use]
pub fn deny(reason: &str) -> String {
    serde_json::json!({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    })
    .to_string()
}

fn real_env(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

/// What the guard prints for one hook event read from `input`: the denial JSON, or `None` when
/// the event is unreadable (not text, not JSON) or the command may run.
pub fn guard_output(mut input: impl Read, env: &dyn Fn(&str) -> Option<String>) -> Option<String> {
    let mut text = String::new();
    input.read_to_string(&mut text).ok()?;
    let event = serde_json::from_str::<serde_json::Value>(&text).ok()?;
    judge_event(&event, env).map(|reason| deny(&reason))
}

/// `goh zsh-split-guard`: the hook event on stdin; a denial on stdout; always exit 0.
#[must_use]
pub fn run_split_guard() -> i32 {
    if let Some(out) = guard_output(std::io::stdin(), &real_env) {
        println!("{out}");
    }
    0
}

/// `goh shell-check`: exit 0 with one line on stderr, or 1 with each problem and its fix.
#[must_use]
pub fn run_shell_check() -> i32 {
    let inputs = check::Inputs {
        env: &real_env,
        is_file: &|p: &Path| p.is_file(),
        probe: &check::real_probe,
    };
    // The harness grep's -v -q (GOH.UGREPVQ): said and recorded, never a failure of its own.
    grep_probe::report(&real_env, &grep_probe::real_grep_probe, &mut |l| {
        goh_tui::eoutln!("{l}");
    });
    match check::check(&inputs) {
        Ok(line) => {
            goh_tui::eoutln!("{OK} goh shell-check: {line}");
            0
        }
        Err(problems) => {
            for problem in &problems {
                goh_tui::eoutln!("{ERR} goh shell-check: {problem}");
            }
            1
        }
    }
}
