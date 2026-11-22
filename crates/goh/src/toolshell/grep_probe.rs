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
    probe_within(execpath, CEILING)
}

/// The probe with its ceiling; a run that outlives it is group-killed (SIGKILL), so it has no exit
/// code and answers `None`, never a guessed code.
fn probe_within(execpath: &str, ceiling: Duration) -> Option<i32> {
    let mut cmd = Command::new(execpath);
    cmd.arg0("ugrep").args(["-G", "-vq", "a"]);
    crate::bounded::run(&mut cmd, Some(b"a\nb\n".to_vec()), ceiling)
        .ok()?
        .code
}

/// The record's directory: `$XDG_STATE_HOME/goh`, else `~/.local/state/goh`.
pub fn state_dir(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let var = |k: &str| env(k).filter(|v| !v.is_empty());
    let base = var("XDG_STATE_HOME").map_or_else(
        || var("HOME").map(|h| PathBuf::from(h).join(".local/state")),
        |x| Some(PathBuf::from(x)),
    )?;
    Some(base.join("goh"))
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
    let dir = state_dir(env);
    let _ = dir.as_ref().map(|d| {
        std::fs::create_dir_all(d)
            .and_then(|()| std::fs::write(d.join("harness_grep.json"), record(&execpath, code)))
    });
    Some(line(&execpath, code))
}

/// `goh shell-check`'s grep line, marked, handed to `say` (nothing when there is no harness binary).
pub fn report(
    env: &dyn Fn(&str) -> Option<String>,
    probe: &dyn Fn(&str) -> Option<i32>,
    say: &mut dyn FnMut(String),
) {
    let marked = probe_and_record(env, probe).map(|(text, warn)| {
        let mark = if warn { goh_tui::WARN } else { goh_tui::OK };
        format!("{mark} goh shell-check: {text}")
    });
    marked.into_iter().for_each(say);
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
        let vars = [
            ("CLAUDE_CODE_EXECPATH", "/x/claude"),
            ("XDG_STATE_HOME", state.as_str()),
        ];
        let env = |k: &str| {
            vars.iter()
                .find(|(n, _)| *n == k)
                .map(|(_, v)| (*v).to_owned())
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

    #[test]
    fn a_probe_past_its_ceiling_answers_none() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("slow");
        std::fs::write(&path, "#!/bin/sh\nsleep 5; exit 0\n").unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
        let slow = path.to_string_lossy().into_owned();
        assert_eq!(probe_within(&slow, Duration::from_millis(200)), None);
    }

    #[test]
    fn an_unprobeable_harness_grep_keeps_the_refusal_and_says_so() {
        let (text, warn) = line("/x/claude", None);
        assert!(warn, "{text}");
        assert!(
            text.contains("could not be probed") && text.contains("refusal stays"),
            "{text}"
        );
        let mut said = Vec::new();
        let env = |k: &str| (k == "CLAUDE_CODE_EXECPATH").then(|| "/x/claude".to_owned());
        report(&env, &|_| None, &mut |l| said.push(l));
        assert_eq!(said.len(), 1, "{said:?}");
        assert!(said[0].contains("could not be probed") && said[0].starts_with(goh_tui::WARN));
        let mut none: Vec<String> = Vec::new();
        report(&|_| None, &|_| Some(0), &mut |l| none.push(l));
        assert_eq!(none, Vec::<String>::new());
    }

    #[test]
    fn the_state_dir_falls_back_to_home_when_xdg_is_unset() {
        let home = |k: &str| (k == "HOME").then(|| "/h".to_owned());
        assert_eq!(state_dir(&home), Some(PathBuf::from("/h/.local/state/goh")));
        let both = |k: &str| Some(if k == "HOME" { "/h" } else { "" }.to_owned());
        assert_eq!(state_dir(&both), Some(PathBuf::from("/h/.local/state/goh")));
        assert_eq!(state_dir(&|_| None), None);
    }
}
