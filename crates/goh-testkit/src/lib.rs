//! Fixture git repos and a runner for the goh binary (a dev-dependency
//! crate: nothing here is dead code in any one test binary, and its lint
//! policy is the test policy - see Cargo.toml). Forbidden glyphs are
//! built from their NUMBERS (`g(0x2705)`), never written as literals or
//! escapes: either would trip the emoji gate on this file. Helpers are not
//! `#[test]` functions, so clippy's test exemption for the restriction lints
//! does not reach them: every helper returns a `Result` and the `#[test]`
//! caller (exempt) unwraps it - no `.expect()` here, no suppression
//! attribute anywhere (the house refuses `#[allow]` and `#[expect]`).
use std::path::{Path, PathBuf};
use std::process::Command;

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
        let st = Command::new("git")
            .args(args)
            .current_dir(self.dir.path())
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
