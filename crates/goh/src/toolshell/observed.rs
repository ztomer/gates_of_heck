//! The shell the harness REALLY runs tool commands in (GOH.SHELLCHECKREAL).
//!
//! `CLAUDE_CODE_SHELL` is a request, not a fact: the Claude desktop app (claude-code embedded)
//! runs `/bin/zsh` for the session and its subagents whatever the setting names (2026-11-21), so a
//! check that judges the setting says "bash 5" while every lane's `${PIPESTATUS[0]}` is empty.
//! Two observations say what actually runs, nearest first:
//!
//! * an ancestor process that is a tool shell: the harness starts each Bash call as
//!   `<shell> -c source <config>/shell-snapshots/snapshot-<shell>-<id>.sh ...`, so when goh runs
//!   inside a tool call that ancestor's `argv[0]` is the shell, beyond doubt. Only a shell that
//!   sourced THIS config's snapshots counts: a test that gives goh its own `HOME` is hermetic
//!   even when the suite itself runs inside a tool call;
//! * else the newest snapshot under `<config>/shell-snapshots/` (`CLAUDE_CONFIG_DIR`, else
//!   `$HOME/.claude`): the harness writes `snapshot-<shell>-<id>.sh` for the shell it runs, so a
//!   hook (whose parent is the harness, not a tool shell) reads the shell from its name.
//!
//! Neither seen, the setting is all there is (`super::tool_shell`).

use std::path::{Path, PathBuf};
use std::process::Command;

/// The shell seen, and where it was seen.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seen {
    /// The shell's path (`/bin/zsh`) from an ancestor, or its name (`zsh`) from a snapshot.
    pub shell: String,
    /// Where: `the Bash tool call's shell (pid N)` or `the newest shell snapshot <path>`.
    pub source: String,
}

/// The ancestors seam: `(pid, command line)` of each ancestor, nearest first.
pub type AncestorsFn<'a> = &'a dyn Fn() -> Vec<(u32, String)>;

/// The tool shell an ancestor's command line names, when it is one that sources a snapshot in
/// `snapshots`.
#[must_use]
pub fn tool_shell_of(command: &str, snapshots: &Path) -> Option<String> {
    let mut words = command.split_whitespace();
    let shell = words.next()?;
    let mark = format!("{}/snapshot-", snapshots.display());
    (words.next() == Some("-c") && command.contains(&mark)).then(|| shell.to_owned())
}

/// The snapshot directory: `CLAUDE_CONFIG_DIR/shell-snapshots`, else `$HOME/.claude/...`.
pub fn snapshot_dir(env: &dyn Fn(&str) -> Option<String>) -> Option<PathBuf> {
    let var = |k: &str| env(k).filter(|v| !v.is_empty());
    let config = var("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .or_else(|| var("HOME").map(|h| Path::new(&h).join(".claude")))?;
    Some(config.join("shell-snapshots"))
}

/// The shell named by the newest `snapshot-<shell>-*.sh` in `dir`, and that file.
#[must_use]
pub fn newest_snapshot(dir: &Path) -> Option<(String, PathBuf)> {
    std::fs::read_dir(dir)
        .ok()?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let name = entry.file_name().into_string().ok()?;
            let rest = name.strip_prefix("snapshot-")?;
            let shell = rest
                .split_once('-')
                .map_or(rest, |(shell, _)| shell)
                .to_owned();
            let mtime = entry.metadata().ok()?.modified().ok()?;
            (!shell.is_empty()).then(|| (mtime, shell, entry.path()))
        })
        .max_by(|a, b| a.0.cmp(&b.0).then_with(|| a.2.cmp(&b.2)))
        .map(|(_, shell, path)| (shell, path))
}

/// The shell seen: a tool-shell ancestor, else the newest snapshot, else `None`.
pub fn seen(env: &dyn Fn(&str) -> Option<String>, ancestors: AncestorsFn) -> Option<Seen> {
    let dir = snapshot_dir(env)?;
    if let Some((pid, shell)) = ancestors()
        .into_iter()
        .find_map(|(pid, cmd)| tool_shell_of(&cmd, &dir).map(|s| (pid, s)))
    {
        return Some(Seen {
            shell,
            source: format!("the Bash tool call's shell (pid {pid})"),
        });
    }
    let (shell, path) = newest_snapshot(&dir)?;
    Some(Seen {
        shell,
        source: format!("the newest shell snapshot {}", path.display()),
    })
}

/// No ancestors: what a hook passes, whose parent is the harness, never a tool shell.
#[must_use]
pub const fn no_ancestors() -> Vec<(u32, String)> {
    Vec::new()
}

/// How long `ps` may take before the ancestors are given up as unseen.
const PS_CEILING: std::time::Duration = std::time::Duration::from_secs(5);

/// The real ancestors, from one `ps -A -o pid=,ppid=,command=`; empty when ps cannot run.
#[must_use]
pub fn real_ancestors() -> Vec<(u32, String)> {
    ancestors_via(Path::new("/bin/ps"))
}

/// The ancestors as `ps` at `ps_path` lists them; empty when it cannot run or overruns.
pub(super) fn ancestors_via(ps_path: &Path) -> Vec<(u32, String)> {
    let mut ps = Command::new(ps_path);
    ps.args(["-A", "-ww", "-o", "pid=,ppid=,command="])
        .stdin(std::process::Stdio::null());
    match crate::bounded::run(&mut ps, None, PS_CEILING) {
        Ok(ran) if !ran.timed_out => ancestors_from(
            &String::from_utf8_lossy(&ran.stdout),
            std::os::unix::process::parent_id(),
        ),
        _ => Vec::new(),
    }
}

/// One `ps` row: `pid ppid command`.
fn parse_row(line: &str) -> Option<(u32, (u32, String))> {
    let line = line.trim_start();
    let (pid, after_pid) = line.split_once(char::is_whitespace)?;
    let after_pid = after_pid.trim_start();
    let (parent, command) = after_pid
        .split_once(char::is_whitespace)
        .unwrap_or((after_pid, ""));
    Some((
        pid.parse().ok()?,
        (parent.parse().ok()?, command.trim_start().to_owned()),
    ))
}

/// The chain from `start` up through `ps` rows (`pid ppid command`), at most 32 deep.
#[must_use]
pub fn ancestors_from(table: &str, start: u32) -> Vec<(u32, String)> {
    let rows: std::collections::BTreeMap<u32, (u32, String)> =
        table.lines().filter_map(parse_row).collect();
    let mut chain = Vec::new();
    let mut at = start;
    while chain.len() < 32 {
        let Some((ppid, cmd)) = rows.get(&at) else {
            break;
        };
        chain.push((at, cmd.clone()));
        if *ppid == at || *ppid == 0 {
            break;
        }
        at = *ppid;
    }
    chain
}
