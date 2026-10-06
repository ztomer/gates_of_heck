//! Fixture git repos and a runner for the goh binary (a dev-dependency
//! crate: nothing here is dead code in any one test binary, and its lint
//! policy is the test policy - see Cargo.toml). Forbidden glyphs are
//! built from their NUMBERS (`g(0x2705)`), never written as literals or
//! escapes: either would trip the emoji gate on this file. Helpers are not
//! `#[test]` functions, so clippy's test exemption for the restriction lints
//! does not reach them: every helper returns a `Result` and the `#[test]`
//! caller (exempt) unwraps it - no `.expect()` here, no suppression
//! attribute anywhere (the house refuses `#[allow]` and `#[expect]`).

#![deny(unsafe_code)]
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

/// The variables that bind a git process to ONE repository.
///
/// As git itself lists them (`git rev-parse --local-env-vars`: what git
/// clears entering a submodule). Asked of git, never copied, so a variable a
/// future git adds is covered the day it ships. Empty only if git cannot run
/// at all - and then every fixture's git fails loudly anyway.
#[must_use]
pub fn local_env_vars() -> &'static [String] {
    static VARS: OnceLock<Vec<String>> = OnceLock::new();
    VARS.get_or_init(|| {
        Command::new("git")
            .args(["rev-parse", "--local-env-vars"])
            .output()
            .map(|o| {
                String::from_utf8_lossy(&o.stdout)
                    .split_whitespace()
                    .map(str::to_owned)
                    .collect()
            })
            .unwrap_or_default()
    })
}

/// Drop the repository-binding variables from `cmd`'s environment, whether
/// inherited or set on it earlier.
///
/// A git hook exports `GIT_DIR` (and `GIT_INDEX_FILE`); under a LINKED
/// worktree `GIT_DIR` is `<main>/.git/worktrees/<name>`, absolute, so a
/// fixture's `git init <tmp>` inheriting it RE-INITIALISES THE REAL
/// REPOSITORY and writes `core.bare = true` into its shared config (zinc,
/// 2026-09-27); a fixture's `config`/`add`/`commit` land there too.
pub fn scrub_repo_env(cmd: &mut Command) -> &mut Command {
    for name in local_env_vars() {
        cmd.env_remove(name);
    }
    cmd
}

/// `git` for a FIXTURE repo: the one way test code spawns git. Not for the
/// goh binary's own git calls, which honour the hook's variables on purpose
/// (staged mode reads the index being committed).
#[must_use]
pub fn git_command() -> Command {
    let mut cmd = Command::new("git");
    scrub_repo_env(&mut cmd);
    cmd
}

/// `git ARGS` in `dir` via [`git_command`], output discarded.
///
/// # Errors
/// git could not run, or exited non-zero.
pub fn git_in(dir: &Path, args: &[&str]) -> Result<(), String> {
    let st = git_command()
        .args(args)
        .current_dir(dir)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map_err(|e| format!("git {args:?}: {e}"))?;
    if st.success() {
        Ok(())
    } else {
        Err(format!("git {args:?} failed: {st}"))
    }
}

/// The character with this codepoint, as a `String` ready to concatenate.
#[must_use]
pub fn g(code: u32) -> String {
    char::from_u32(code).map_or_else(|| "\u{FFFD}".to_owned(), |c| c.to_string())
}

/// Escape TEXT (a backslash followed by `rest`), assembled so that no
/// escape sequence appears in this source file.
#[must_use]
pub fn esc(rest: &str) -> String {
    format!("{}{rest}", char::from(92u8))
}

#[derive(Debug)]
pub struct Out {
    pub status: std::process::ExitStatus,
    pub stdout: String,
    pub stderr: String,
}

impl Out {
    /// Both streams: the verdicts go to stdout (like the Python checkers)
    /// and the structural pipeline's step failures to stderr.
    #[must_use]
    pub fn text(&self) -> String {
        format!("{}{}", self.stdout, self.stderr)
    }
}

pub struct Repo {
    dir: tempfile::TempDir,
}

impl Repo {
    /// A directory that is NOT a git repository.
    #[must_use]
    pub const fn bare(dir: tempfile::TempDir) -> Self {
        Self { dir }
    }

    #[must_use]
    pub fn path(&self) -> &Path {
        self.dir.path()
    }

    /// Write `rel` under the repo (creating parents).
    ///
    /// # Errors
    /// The directory or the file could not be written.
    pub fn write(&self, rel: &str, content: &str) -> std::io::Result<()> {
        let p = self.dir.path().join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(p, content)
    }

    /// `git ARGS` in the repo, quietly.
    ///
    /// # Errors
    /// git could not run, or exited non-zero.
    pub fn git(&self, args: &[&str]) -> Result<(), String> {
        git_in(self.dir.path(), args)
    }
}

/// A git repo with `files` written AND STAGED (staged, not committed, so
/// both scopes see them and `--staged` has something to police).
///
/// # Errors
/// The temp dir, a file or a git step failed - the fixture is unusable.
pub fn repo_with(files: &[(&str, &str)]) -> Result<Repo, String> {
    let dir = tempfile::tempdir().map_err(|e| format!("tempdir: {e}"))?;
    let r = Repo { dir };
    r.git(&["init", "-q"])?;
    r.git(&["config", "user.email", "t@t"])?;
    r.git(&["config", "user.name", "t"])?;
    for (rel, content) in files {
        r.write(rel, content)
            .map_err(|e| format!("write {rel}: {e}"))?;
    }
    r.git(&["add", "-A"])?;
    Ok(r)
}

fn goh_root() -> std::io::Result<PathBuf> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
}

/// Run the goh binary at `bin`.
///
/// The test passes `env!("CARGO_BIN_EXE_goh")`, which only the goh crate's
/// own tests see. Runs in `repo` (or the current dir) with `GOH_DIR`
/// pointing at this checkout, so delegated Python checkers resolve. A
/// missing binary or a spawn failure is a test failure, never a skip.
///
/// # Errors
/// The checkout root could not be resolved or the binary did not run.
pub fn goh_at(
    bin: &Path,
    repo: Option<&Repo>,
    args: &[&str],
    envs: &[(&str, &str)],
) -> std::io::Result<Out> {
    let mut cmd = Command::new(bin);
    // The binary runs git in the FIXTURE: the hook's repository variables
    // would point it at the real repo. A test that wants a hook's variable
    // passes it in `envs`, applied after the scrub.
    scrub_repo_env(&mut cmd);
    cmd.args(args)
        .env("GOH_DIR", goh_root()?)
        .env_remove("GOH_BIN")
        .env_remove("GOH_NO_NATIVE");
    if let Some(r) = repo {
        cmd.current_dir(r.path());
    }
    for (k, v) in envs {
        cmd.env(k, v);
    }
    let out = cmd.output()?;
    Ok(Out {
        status: out.status,
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    })
}
