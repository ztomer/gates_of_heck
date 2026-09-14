//! Fixture git repos and a runner for the goh binary. Forbidden glyphs are
//! built from their NUMBERS (`g(0x2705)`), never written as literals or
//! escapes: either would trip the emoji gate on this file. Helpers are not
//! `#[test]` functions, so clippy's test exemption for the restriction lints
//! does not reach them; a fixture that cannot be built has nothing to do but
//! panic, hence the `#[expect]`s.
use std::path::{Path, PathBuf};
use std::process::Command;

/// The character with this codepoint, as a `String` ready to concatenate.
#[must_use]
#[expect(clippy::expect_used)]
pub fn g(code: u32) -> String {
    char::from_u32(code).expect("scalar value").to_string()
}

/// Escape TEXT (a backslash followed by `rest`), assembled so that no
/// escape sequence appears in this source file.
#[must_use]
pub fn esc(rest: &str) -> String {
    format!("{}{rest}", char::from(92u8))
}

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
    pub const fn bare(dir: tempfile::TempDir) -> Self {
        Self { dir }
    }

    pub fn path(&self) -> &Path {
        self.dir.path()
    }

    #[expect(clippy::expect_used)]
    pub fn write(&self, rel: &str, content: &str) {
        let p = self.dir.path().join(rel);
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).expect("mkdir");
        }
        std::fs::write(p, content).expect("write");
    }

    #[expect(clippy::expect_used)]
    pub fn git(&self, args: &[&str]) {
        let st = Command::new("git")
            .args(args)
            .current_dir(self.dir.path())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .expect("git runs");
        assert!(st.success(), "git {args:?} failed");
    }
}

/// A git repo with `files` written AND STAGED (staged, not committed, so
/// both scopes see them and `--staged` has something to police).
#[expect(clippy::expect_used)]
pub fn repo_with(files: &[(&str, &str)]) -> Repo {
    let dir = tempfile::tempdir().expect("tempdir");
    let r = Repo { dir };
    r.git(&["init", "-q"]);
    r.git(&["config", "user.email", "t@t"]);
    r.git(&["config", "user.name", "t"]);
    for (rel, content) in files {
        r.write(rel, content);
    }
    r.git(&["add", "-A"]);
    r
}

#[expect(clippy::expect_used)]
fn goh_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("repo root")
}

/// Run the binary in `repo` (or the current dir) with `GOH_DIR` pointing at
/// this checkout, so delegated Python checkers resolve. A missing binary
/// or a spawn failure is a test failure, never a skip.
#[expect(clippy::expect_used)]
pub fn goh(repo: Option<&Repo>, args: &[&str], envs: &[(&str, &str)]) -> Out {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_goh"));
    cmd.args(args)
        .env("GOH_DIR", goh_root())
        .env_remove("GOH_BIN")
        .env_remove("GOH_NO_NATIVE");
    if let Some(r) = repo {
        cmd.current_dir(r.path());
    }
    for (k, v) in envs {
        cmd.env(k, v);
    }
    let out = cmd.output().expect("goh runs");
    Out {
        status: out.status,
        stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
    }
}
