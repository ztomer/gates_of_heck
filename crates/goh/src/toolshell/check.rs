//! `goh shell-check`: the agent tool shell is bash >= 5 and its `BASH_ENV` builds the house env.
//!
//! The cause of the zsh no-word-split trap is removed by running agents' commands in bash
//! (owner 2026-11-21): `CLAUDE_CODE_SHELL=/opt/homebrew/bin/bash` and `BASH_ENV=~/.bashrc` in
//! `~/.claude/settings.json`, the rc loading the environment a clean zsh login builds. Each part
//! can rot on its own -- a settings edit drops a key, a brew cleanup removes bash 5, the rc stops
//! loading -- and the session then runs zsh, or a bash with no `GOH_DIR`, with nothing said. This
//! check says it at session start. Every input is a seam: the environment, the file test and the
//! probe that runs `<shell> -c` under the named `BASH_ENV` in an otherwise EMPTY environment, so
//! what it reads is what the rc builds, never what the hook inherited.

use std::path::Path;
use std::process::{Command, Stdio};
use std::time::Duration;

/// What one `<shell> -c` under `BASH_ENV` reported.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Probed {
    /// `BASH_VERSINFO[0]`; `None` when the shell is not bash.
    pub bash_major: Option<u32>,
    pub bash_version: String,
    pub goh_dir: String,
    pub pythonpath: String,
    pub agent_env_loaded: String,
}

/// The probe's script: five NUL-terminated fields, empty when unset.
const SCRIPT: &str = r#"printf '%s\0%s\0%s\0%s\0%s\0' "${BASH_VERSINFO[0]:-}" "${BASH_VERSION:-}" "${GOH_DIR:-}" "${PYTHONPATH:-}" "${AGENT_ENV_LOADED:-}""#;

/// How long the probe may take: the rc may rebuild its cache with a full `zsh -lic`.
const PROBE_CEILING: Duration = Duration::from_secs(25);

/// The fields of the probe's output.
pub fn parse_probe(out: &[u8]) -> Probed {
    let text = String::from_utf8_lossy(out);
    let mut f = text.split('\0').map(str::to_owned);
    let mut next = || f.next().unwrap_or_default();
    let major = next();
    Probed {
        bash_major: major.parse().ok(),
        bash_version: next(),
        goh_dir: next(),
        pythonpath: next(),
        agent_env_loaded: next(),
    }
}

/// The real probe: `<shell> -c SCRIPT` with only `HOME`, `USER`, `PATH` and (when given) `BASH_ENV` set.
///
/// # Errors
/// When the shell cannot be run or the run hits its ceiling.
pub fn real_probe(shell: &str, bash_env: Option<&str>, home: &str) -> Result<Probed, String> {
    let mut cmd = Command::new(shell);
    cmd.arg("-c")
        .arg(SCRIPT)
        .env_clear()
        .env("HOME", home)
        .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        .stdin(Stdio::null());
    if let Ok(user) = std::env::var("USER") {
        cmd.env("USER", user);
    }
    if let Some(rc) = bash_env {
        cmd.env("BASH_ENV", rc);
    }
    let ran = crate::bounded::run(&mut cmd, None, PROBE_CEILING)
        .map_err(|e| format!("cannot run {shell}: {e}"))?;
    if ran.timed_out {
        return Err(format!(
            "{shell} -c did not finish in {}s",
            PROBE_CEILING.as_secs()
        ));
    }
    Ok(parse_probe(&ran.stdout))
}

/// The probe seam: `(shell, BASH_ENV, HOME)` to what the shell reported, or why it could not run.
pub type ProbeFn<'a> = &'a dyn Fn(&str, Option<&str>, &str) -> Result<Probed, String>;

/// The inputs the check reads.
pub struct Inputs<'a> {
    pub env: &'a dyn Fn(&str) -> Option<String>,
    pub is_file: &'a dyn Fn(&Path) -> bool,
    pub probe: ProbeFn<'a>,
}

/// The verdict: the one line that says all is well.
///
/// # Errors
/// Every problem found, each naming its fix.
pub fn check(inputs: &Inputs) -> Result<String, Vec<String>> {
    let var = |k: &str| (inputs.env)(k).filter(|v| !v.is_empty());
    let mut problems = Vec::new();
    let home = var("HOME").unwrap_or_default();
    let bash_env = var("BASH_ENV");
    let rc = bash_env
        .as_deref()
        .filter(|p| (inputs.is_file)(Path::new(p)));
    match &bash_env {
        None => problems.push(
            "BASH_ENV is not set, so a non-interactive bash starts with no house env (no GOH_DIR, \
             PYTHONPATH or PATH): set env.BASH_ENV to ~/.bashrc in ~/.claude/settings.json"
                .to_owned(),
        ),
        Some(path) if rc.is_none() => problems.push(format!(
            "BASH_ENV={path} is not a file: point env.BASH_ENV in ~/.claude/settings.json at \
             the rc that loads the agent env (~/.bashrc)"
        )),
        Some(_) => {}
    }
    let Some(shell) = var("CLAUDE_CODE_SHELL") else {
        let login = var("SHELL").unwrap_or_else(|| "unset".to_owned());
        problems.push(format!(
            "CLAUDE_CODE_SHELL is not set, so the agent tool shell is SHELL={login}, and zsh does \
             not word-split an unquoted $VAR: set env.CLAUDE_CODE_SHELL to a bash >= 5 \
             (/opt/homebrew/bin/bash) in ~/.claude/settings.json"
        ));
        return Err(problems);
    };
    let probed = match (inputs.probe)(&shell, rc, &home) {
        Ok(p) => p,
        Err(why) => {
            problems.push(format!(
                "CLAUDE_CODE_SHELL={shell} cannot be probed ({why}): point it at a bash >= 5 \
                 (brew install bash; /opt/homebrew/bin/bash)"
            ));
            return Err(problems);
        }
    };
    match probed.bash_major {
        None => problems.push(format!(
            "CLAUDE_CODE_SHELL={shell} is not bash: point it at a bash >= 5 \
             (/opt/homebrew/bin/bash)"
        )),
        Some(major) if major < 5 => problems.push(format!(
            "CLAUDE_CODE_SHELL={shell} is bash {}, older than 5: brew install bash and point \
             it at /opt/homebrew/bin/bash",
            probed.bash_version
        )),
        Some(_) => {}
    }
    if let Some(rc) = rc {
        env_problems(&probed, &shell, rc, &mut problems);
    }
    if problems.is_empty() {
        Ok(format!(
            "agent tool shell: bash {} ({shell}); BASH_ENV={} gives GOH_DIR={}",
            probed.bash_version,
            rc.unwrap_or_default(),
            probed.goh_dir
        ))
    } else {
        Err(problems)
    }
}

fn env_problems(probed: &Probed, shell: &str, rc: &str, problems: &mut Vec<String>) {
    let run = format!("`{shell} -c` under BASH_ENV={rc}");
    if probed.agent_env_loaded != "1" {
        problems.push(format!(
            "{run} did not load the agent env (AGENT_ENV_LOADED is not 1): the rc must load \
             ~/.cache/agent-env/env.bash and export AGENT_ENV_LOADED=1"
        ));
    }
    if probed.goh_dir.is_empty() {
        problems.push(format!(
            "{run} has no GOH_DIR: export GOH_DIR in ~/.zshenv or ~/.zprofile (the agent env is \
             the env of a clean `zsh -lic`)"
        ));
    } else if !probed.pythonpath.split(':').any(|p| p == probed.goh_dir) {
        problems.push(format!(
            "{run} has PYTHONPATH={} without GOH_DIR={}: add GOH_DIR to PYTHONPATH in the zsh \
             startup files",
            probed.pythonpath, probed.goh_dir
        ));
    }
}
