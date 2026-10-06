//! Shell lint -- Rust port of `checks/check_shell_lint.sh` (Phase N1).
//!
//! Every tracked `*.sh` and `hooks/*` file (at `--staged`, the staged blobs,
//! checked out of the index into a temp dir) must pass `bash -n`, and the
//! syntactically clean ones must pass `shellcheck --severity=error` -- one
//! shellcheck call for all of them. A missing shellcheck is a refusal, not a
//! pass. Every spawn runs under the step ceiling (`crate::bounded`).

use std::path::{Path, PathBuf};
use std::process::Command;

type Lines = Vec<(bool, String)>;
/// `(display path, path to read)` for each shell file in scope.
type Files = Vec<(String, PathBuf)>;

fn git_z(root: &Path, args: &[&str]) -> Result<Vec<String>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    Ok(out
        .stdout
        .split(|b| *b == 0)
        .filter(|n| !n.is_empty())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .collect())
}

fn in_scope(f: &str, exclude: Option<&regex::Regex>) -> bool {
    (f.as_bytes().ends_with(b".sh") || f.starts_with("hooks/"))
        && !exclude.is_some_and(|x| x.is_match(f))
}

/// A temp dir removed when dropped.
struct TempDir(PathBuf);

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn temp_dir() -> Result<TempDir, String> {
    let base = std::env::var_os("TMPDIR").map_or_else(|| PathBuf::from("/tmp"), PathBuf::from);
    for n in 0..1000u32 {
        let dir = base.join(format!("shell-lint.{}.{n}", std::process::id()));
        if std::fs::create_dir(&dir).is_ok() {
            return Ok(TempDir(dir));
        }
    }
    Err("cannot make a temp dir for the staged blobs".to_owned())
}

fn bounded(cmd: &mut Command) -> Result<crate::bounded::Ran, String> {
    let ceiling = crate::bounded::step_ceiling();
    let ran = crate::bounded::run(cmd, None, ceiling).map_err(|e| e.to_string())?;
    if ran.timed_out {
        return Err(format!("TIMED OUT after {}s", ceiling.as_secs()));
    }
    Ok(ran)
}

/// The shell files in scope, `(display path, path to read)`, and the temp dir
/// the staged blobs were checked out into (held until the judgement is done).
fn gather(
    root: &Path,
    staged: bool,
    exclude: Option<&regex::Regex>,
) -> Result<(Files, Option<TempDir>), String> {
    if !staged {
        let files = git_z(root, &["ls-files", "-z"])?
            .into_iter()
            .filter(|f| in_scope(f, exclude))
            .map(|f| {
                let src = root.join(&f);
                (f, src)
            })
            .collect();
        return Ok((files, None));
    }
    let picked: Vec<String> = git_z(
        root,
        &[
            "diff",
            "--cached",
            "--name-only",
            "-z",
            "--diff-filter=ACMR",
        ],
    )?
    .into_iter()
    .filter(|f| in_scope(f, exclude))
    .collect();
    if picked.is_empty() {
        return Ok((Vec::new(), None));
    }
    let tmp = temp_dir()?;
    let prefix = format!("{}/index/", tmp.0.display());
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(root).args([
        "checkout-index",
        "-z",
        "--stdin",
        &format!("--prefix={prefix}"),
    ]);
    let list: Vec<u8> = picked.iter().flat_map(|f| f.bytes().chain([0])).collect();
    let _ = crate::bounded::run(&mut cmd, Some(list), crate::bounded::step_ceiling());
    let files = picked
        .into_iter()
        .filter_map(|f| {
            let src = tmp.0.join("index").join(&f);
            src.is_file().then_some((f, src))
        })
        .collect();
    Ok((files, Some(tmp)))
}

/// `bash -n` each file, then one shellcheck over the clean ones: `(failure
/// lines, files that failed)` in the reference's order.
fn judge(files: &[(String, PathBuf)]) -> Result<(Vec<String>, Vec<String>), String> {
    let (mut fail, mut failed): (Vec<String>, Vec<String>) = (Vec::new(), Vec::new());
    let mark = |failed: &mut Vec<String>, name: &str| {
        if !failed.iter().any(|f| f == name) {
            failed.push(name.to_owned());
        }
    };
    let mut clean: Vec<&(String, PathBuf)> = Vec::new();
    for entry in files {
        let ran = bounded(Command::new("bash").arg("-n").arg(&entry.1))
            .map_err(|e| format!("bash -n {}: {e}", entry.0))?;
        if ran.code == Some(0) {
            clean.push(entry);
        } else {
            let err = String::from_utf8_lossy(&ran.stderr);
            fail.push(format!(
                "{}: bash -n: {}",
                entry.0,
                err.lines().next().unwrap_or("")
            ));
            mark(&mut failed, &entry.0);
        }
    }
    if clean.is_empty() {
        return Ok((fail, failed));
    }
    let mut cmd = Command::new("shellcheck");
    cmd.args(["--severity=error", "--format=gcc"])
        .args(clean.iter().map(|e| &e.1));
    let ran = bounded(&mut cmd).map_err(|e| format!("shellcheck: {e}"))?;
    let mut out = String::from_utf8_lossy(&ran.stdout).into_owned();
    out.push_str(&String::from_utf8_lossy(&ran.stderr));
    let mut out = out.trim_end_matches('\n').to_owned();
    if !out.is_empty() {
        for (disp, src) in &clean {
            out = out.replace(&*src.to_string_lossy(), disp);
        }
        for line in out.split('\n') {
            fail.push(line.to_owned());
            mark(&mut failed, line.split(':').next().unwrap_or(line));
        }
    }
    Ok((fail, failed))
}

fn run(staged: bool, exclude: &str) -> (i32, Lines) {
    let exclude = match crate::steps::compile_exclude(exclude) {
        Ok(x) => x,
        Err(m) => return (2, vec![(true, format!("✗ [shell_lint] {m}"))]),
    };
    let Some(root) = crate::gitutil::repo_root().map(PathBuf::from) else {
        return (
            0,
            vec![(false, "[shell_lint] not a git repo — skipping".to_owned())],
        );
    };
    let (files, tmp) = match gather(&root, staged, exclude.as_ref()) {
        Ok(got) => got,
        Err(e) => return (2, vec![(true, format!("✗ [shell_lint] {e}"))]),
    };
    if files.is_empty() {
        return (
            0,
            vec![(
                false,
                "✓ [shell_lint] OK — no shell files in scope".to_owned(),
            )],
        );
    }
    if !crate::pyformat::on_path("shellcheck") {
        return (1, vec![(true, "✗ [shell_lint] shellcheck is not installed, and this gate does not pass without it -- brew install shellcheck".to_owned())]);
    }
    let verdict = judge(&files);
    drop(tmp);
    let (fail, failed) = match verdict {
        Ok(v) => v,
        Err(e) => return (1, vec![(true, format!("✗ [shell_lint] {e}"))]),
    };
    let scope = if staged { "staged" } else { "tracked" };
    if !failed.is_empty() {
        let mut lines = vec![(
            true,
            format!("✗ [shell_lint] {} file(s) failed ({scope}):", failed.len()),
        )];
        lines.extend(fail.into_iter().map(|f| (true, format!("  {f}"))));
        return (1, lines);
    }
    (
        0,
        vec![(
            false,
            format!(
                "✓ [shell_lint] OK — {} {scope} shell files clean",
                files.len()
            ),
        )],
    )
}

/// `goh shell-lint [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let (code, lines) = run(staged, exclude);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// The structural step; the delegated step's labels.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let label = if staged {
        "shell lint (staged)"
    } else {
        "shell lint"
    };
    let start = crate::step_report::begin(label);
    match run(staged, &cfg.exclude) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/shell_lint.rs", "check_shell_lint.sh"),
                &text,
                start,
            );
            Some(code)
        }
    }
}
