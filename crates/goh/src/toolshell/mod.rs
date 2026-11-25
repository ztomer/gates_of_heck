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
//!   It also refuses `PIPESTATUS`, which zsh leaves empty (its array is `pipestatus`).
//!   The escape is `# zsh-split-ok: <reason>`.
//!
//! Both judge the shell the harness REALLY runs (`observed`), never only the setting: the Claude
//! desktop app runs zsh whatever `CLAUDE_CODE_SHELL` names (GOH.SHELLCHECKREAL).
//
// `goh shell-check`, `goh zsh-split-guard`: The agent tool shell (GOH.ZSHSPLITGUARD, owner
// 2026-11-21). zsh does not word-split an unquoted `$VAR`, and three commands ran wrong in one day
// (`F="a b"; git commit -- $F`; `L="env A=1 python x.py"; $L done X` ran nothing 20 times; `for f
// in $FILES` overwrote a lane's uncommitted edits). The cause is removed by running agent commands
// in bash 5 (`CLAUDE_CODE_SHELL`, `BASH_ENV=~/.bashrc` in settings.json). `goh shell-check`
// (SessionStart hook, `check.rs`) exits 1 naming each fix unless `CLAUDE_CODE_SHELL` is a bash >=
// 5, `BASH_ENV` is a file, and a `<shell> -c` under it in an EMPTY environment yields `GOH_DIR`, a
// `PYTHONPATH` holding it and `AGENT_ENV_LOADED=1`; env, file test and probe are seams. `goh
// zsh-split-guard` (PreToolUse on Bash and Monitor, `split.rs`) parses the command with
// `brush-parser` and, ONLY while the tool shell (`CLAUDE_CODE_SHELL`, else `SHELL`) is zsh, denies
// an unquoted plain `$VAR`/`${VAR}` used as a command word, a `for` list or an argument after the
// same command assigned VAR a whitespace-holding value (standalone or
// `export`/`local`/`typeset`/`declare`/`readonly`; an array assignment or a whitespace-free one
// clears it; a prefix `F=.. cmd` is cmd's env, not the shell's). The refusal names the fixes: an
// array, a function, the words inline. Escape `# zsh-split-ok: <reason>`. What does not parse runs,
// unjudged. Both judge the shell the harness REALLY runs (`observed.rs`, GOH.SHELLCHECKREAL), not
// the setting: the Claude desktop app runs zsh whatever `CLAUDE_CODE_SHELL` names. A tool-shell
// ancestor (`<shell> -c source <config>/shell-snapshots/snapshot-...`, this config's only) wins,
// else the newest `snapshot-<shell>-*.sh` under `CLAUDE_CONFIG_DIR` or `~/.claude`, else the
// setting; `shell-check` is red naming the remedy when the seen shell is not bash, and the guard
// also refuses `$PIPESTATUS` under zsh (use `${pipestatus[1]}` or capture rc before the pipe). Both
// are `"$GOH_DIR/bin/goh" <sub>` entries in `hooks/claude/registrations.json`, which
// `scripts/apply_claude_hooks.py` keys as `goh <sub>`. Lies: a list built by command substitution
// (`F=$(git ls-files)`) is not seen; nor a use inside `$(...)` or `eval`. Pinned by
// `toolshell/tests.rs`, `toolshell/observed_tests.rs`, `test_zsh_split_guard.py`,
// `test_claude_hook_registrations.py`, `test_apply_claude_hooks.py`.

pub mod check;
pub mod grep_probe;
pub mod observed;
pub mod split;

#[cfg(test)]
mod observed_tests;
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

/// The shell a hook judges: the one seen (`observed::seen`, snapshots only: a hook's parent is
/// the harness), else the setting.
pub fn effective_shell(env: &dyn Fn(&str) -> Option<String>) -> String {
    observed::seen(env, &observed::no_ancestors).map_or_else(|| tool_shell(env), |s| s.shell)
}

/// A bash by its file name (`/opt/homebrew/bin/bash`, `bash`).
#[must_use]
pub fn is_bash(shell: &str) -> bool {
    Path::new(shell)
        .file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.starts_with("bash"))
}

/// Whether `command` expands `PIPESTATUS` (`$PIPESTATUS`, `${PIPESTATUS[0]}`, `${#PIPESTATUS}`).
#[must_use]
pub fn uses_pipestatus(command: &str) -> bool {
    command.match_indices("PIPESTATUS").any(|(at, _)| {
        let before = command[..at].trim_end_matches(['{', '#', '!']);
        let after = command[at + "PIPESTATUS".len()..].chars().next();
        before.ends_with('$') && !after.is_some_and(|c| c.is_ascii_alphanumeric() || c == '_')
    })
}

/// The refusal for `PIPESTATUS` under zsh.
#[must_use]
pub fn pipestatus_reason(shell: &str) -> String {
    format!(
        "The tool shell is zsh ({shell}), where PIPESTATUS is unset: `${{PIPESTATUS[0]}}` expands \
         to nothing, and `cmd | tail` exits with tail's status, so the command reads an exit code \
         that is not there. Use zsh's own array (`${{pipestatus[1]}}`, 1-based), or capture the \
         code before any pipe (`cmd > out.log 2>&1; rc=$?; tail out.log; exit $rc`). The \
         CLAUDE_CODE_SHELL setting is not honoured by the desktop app (`goh shell-check` says \
         which shell really runs). To run it as written on purpose, add \
         `# zsh-split-ok: <reason>` to the command."
    )
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
    let shell = effective_shell(env);
    if !is_zsh(&shell) || escaped(command) {
        return None;
    }
    if uses_pipestatus(command) {
        return Some(pipestatus_reason(&shell));
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
        ancestors: &observed::real_ancestors,
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
