//! The harness grep's `-v -q` probe (GOH.UGREPVQ, 2026-11-21).
//!
//! Claude Code defines `grep` in every tool shell as a function over its embedded ugrep
//! (`exec -a ugrep <claude binary> -G ...`), which exits 1 for `-v -q` while a non-matching line
//! exists (POSIX grep exits 0). `goh shell-check` runs the repro against the binary in
//! `CLAUDE_CODE_EXECPATH` -- `printf 'a\nb\n' | ugrep -G -vq a` -- says what it answered, and
//! records it in `$XDG_STATE_HOME/goh/harness_grep.json` (`{"execpath", "vq_exit"}`), which the
//! Bash guard (`lib/harness_grep.py`) reads: a record of 0 for the binary in use lifts its refusal.

use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

const CEILING: Duration = Duration::from_secs(10);

/// The probe's exit code from the binary at `execpath` run as ugrep; `None` when it could not run.
#[must_use]
pub fn real_grep_probe(execpath: &str) -> Option<i32> {
    let mut cmd = Command::new(execpath);
    cmd.arg0("ugrep").args(["-G", "-vq", "a"]);
    let ran = crate::bounded::run(&mut cmd, Some(b"a\nb\n".to_vec()), CEILING).ok()?;
    if ran.timed_out {
        return None;
    }
    ran.code
}

/// Where the record goes: `$XDG_STATE_HOME/goh/harness_grep.json`, else under `~/.local/state`.
pub fn state_path(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let var = |k: &str| env(k).filter(|v| !v.is_empty());
    let base = var("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| var("HOME").map(|h| PathBuf::from(h).join(".local/state")))?;
    Some(base.join("goh").join("harness_grep.json"))
}

/// The record the guard reads.
#[must_use]
pub fn record(execpath: &str, code: Option<i32>) -> String {
    serde_json::json!({ "execpath": execpath, "vq_exit": code }).to_string()
}

/// The line shell-check prints, and whether it is a warning (the bug is present or unprobed).
#[must_use]
pub fn line(execpath: &str, code: Option<i32>) -> (String, bool) {
    match code {
        Some(0) => (
            format!(
                "harness grep -v -q answers 0 ({execpath}): the Bash guard's -v -q refusal is lifted"
            ),
            false,
        ),
        Some(c) => (
            format!(
                "harness grep -v -q answers {c} where POSIX grep answers 0 ({execpath}, \
                 `printf 'a\\nb\\n' | grep -vq a`): the Bash guard refuses `grep -v -q`; use \
                 `command grep` (GOH.UGREPVQ; the upstream report is the owner's to send)"
            ),
            true,
        ),
        None => (
            format!("harness grep could not be probed ({execpath}): the -v -q refusal stays"),
            true,
        ),
    }
}

/// Probe, record and describe: `None` when there is no harness binary to probe.
pub fn probe_and_record(
    env: &dyn Fn(&str) -> Option<String>,
    probe: &dyn Fn(&str) -> Option<i32>,
) -> Option<(String, bool)> {
    let execpath = env("CLAUDE_CODE_EXECPATH").filter(|v| !v.is_empty())?;
    let code = probe(&execpath);
    if let Some(path) = state_path(env) {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(&path, record(&execpath, code));
    }
    Some(line(&execpath, code))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    fn fake(dir: &std::path::Path, exit: i32) -> String {
        let path = dir.join(format!("claude{exit}"));
        std::fs::write(&path, format!("#!/bin/sh\ncat >/dev/null\nexit {exit}\n")).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        path.to_string_lossy().into_owned()
    }

    #[test]
    fn the_probe_reports_the_binarys_exit_code() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(real_grep_probe(&fake(dir.path(), 1)), Some(1));
        assert_eq!(real_grep_probe(&fake(dir.path(), 0)), Some(0));
        assert_eq!(real_grep_probe("/nonexistent/claude"), None);
    }

    #[test]
    fn shell_check_records_the_probe_where_the_guard_reads_it() {
        let dir = tempfile::tempdir().unwrap();
        let state = dir.path().to_string_lossy().into_owned();
        let env = |k: &str| match k {
            "CLAUDE_CODE_EXECPATH" => Some("/x/claude".to_owned()),
            "XDG_STATE_HOME" => Some(state.clone()),
            _ => None,
        };
        let (text, warn) = probe_and_record(&env, &|_| Some(1)).unwrap();
        assert!(
            warn && text.contains("answers 1") && text.contains("command grep"),
            "{text}"
        );
        let saved = std::fs::read_to_string(dir.path().join("goh/harness_grep.json")).unwrap();
        let rec: serde_json::Value = serde_json::from_str(&saved).unwrap();
        assert_eq!(rec["execpath"], "/x/claude");
        assert_eq!(rec["vq_exit"], 1);
        let (text, warn) = probe_and_record(&env, &|_| Some(0)).unwrap();
        assert!(!warn && text.contains("lifted"), "{text}");
        assert!(probe_and_record(&|_| None, &|_| Some(0)).is_none());
    }
}
