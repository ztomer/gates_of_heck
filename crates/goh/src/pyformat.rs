//! Python shape, by the repo's own declared rule set -- Rust port of
//! `checks/check_python_formatted.py` (Phase N1).
//!
//! `ruff format --check` over the repo's Python (git's worktree listing, so
//! an untracked or ignored file is not the repo's), or at `--staged` over
//! each staged blob through `--stdin-filename`, concurrently, reformatting
//! nothing. A missing ruff is a missing gate, not a pass. Every ruff runs
//! under the step ceiling (`crate::bounded`).

use std::path::{Path, PathBuf};
use std::process::Command;

type Lines = Vec<(bool, String)>;

fn ruff(root: &Path, args: &[&str], input: Option<Vec<u8>>) -> Result<(i32, String), String> {
    let mut cmd = Command::new("ruff");
    cmd.args(["format", "--check"]).args(args).current_dir(root);
    let ran = crate::bounded::run(&mut cmd, input, crate::bounded::step_ceiling())
        .map_err(|e| format!("cannot start ruff: {e}"))?;
    if ran.timed_out {
        return Err(format!(
            "TIMED OUT after {}s: ruff format --check",
            crate::bounded::step_ceiling().as_secs()
        ));
    }
    let mut text = String::from_utf8_lossy(&ran.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&ran.stderr));
    Ok((ran.code.unwrap_or(1), text.trim().to_owned()))
}

/// Is `tool` an executable on `PATH` (`shutil.which`)?
pub(crate) fn on_path(tool: &str) -> bool {
    std::env::var_os("PATH").is_some_and(|paths| {
        std::env::split_paths(&paths).any(|d| {
            let p = d.join(tool);
            p.is_file()
                && std::fs::metadata(&p).is_ok_and(|m| {
                    std::os::unix::fs::PermissionsExt::mode(&m.permissions()) & 0o111 != 0
                })
        })
    })
}

fn root() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_default();
    crate::gitutil::repo_root().map_or(cwd, |r| {
        let p = PathBuf::from(r);
        p.canonicalize().unwrap_or(p)
    })
}

/// `(rel, ruff's exit code, its output)` for one staged blob.
type Judged = (String, i32, String);

fn judge_blob(root: &Path, rel: &str) -> Result<Option<Judged>, String> {
    let Some(blob) = crate::gitutil::content_bytes(root, rel, true) else {
        return Ok(None);
    };
    let (code, text) = ruff(
        root,
        &["--force-exclude", "--stdin-filename", rel, "-"],
        Some(blob),
    )?;
    Ok(Some((rel.to_owned(), code, text)))
}

fn staged(root: &Path) -> Result<Lines, String> {
    let paths: Vec<String> = crate::gitutil::listed_files(root, true)?
        .into_iter()
        .filter(|f| f.as_bytes().ends_with(b".py"))
        .collect();
    // Up to eight rufs at once, each spawned before any is joined: one ruff start per commit's
    // worth of files, not one per file in a row.
    let results: Vec<Result<Option<Judged>, String>> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for chunk in paths.chunks(paths.len().div_ceil(8).max(1)) {
            handles.push(scope.spawn(move || {
                chunk
                    .iter()
                    .map(|rel| judge_blob(root, rel))
                    .collect::<Vec<_>>()
            }));
        }
        let mut all = Vec::new();
        for h in handles {
            all.extend(h.join().unwrap_or_default());
        }
        all
    });
    let mut judged = Vec::new();
    for r in results {
        if let Some(row) = r? {
            judged.push(row);
        }
    }
    if judged.is_empty() {
        return Ok(vec![(
            false,
            "→ not applicable: no staged Python files -- nothing for the formatter to judge"
                .to_owned(),
        )]);
    }
    let bad: Vec<&(String, i32, String)> = judged.iter().filter(|(_, c, _)| *c != 0).collect();
    if bad.is_empty() {
        return Ok(vec![(
            false,
            format!(
                "✓ every staged Python file ({}) is ruff-formatted",
                judged.len()
            ),
        )]);
    }
    let mut out: Lines = bad
        .iter()
        .map(|(rel, _, text)| {
            (
                true,
                format!(
                    "✗   {rel}: {}",
                    text.lines().next().unwrap_or("would reformat")
                ),
            )
        })
        .collect();
    out.push((
        true,
        format!(
            "✗ {} of {} staged Python file(s) are not ruff-formatted. Run your repo's fmt target (`ruff format`) and re-stage -- do not hand-fix the shape.",
            bad.len(),
            judged.len()
        ),
    ));
    Ok(out)
}

fn full(root: &Path, trees: &[String]) -> Result<(i32, Lines), String> {
    let first = trees.first().map_or(".", String::as_str);
    let prefix = if first == "." || first.is_empty() {
        String::new()
    } else {
        format!("{}/", first.trim_end_matches('/'))
    };
    let any = crate::gitutil::listed_files(root, false)
        .unwrap_or_default()
        .iter()
        .any(|f| f.starts_with(&prefix) && f.as_bytes().ends_with(b".py"));
    let named = trees.join(", ");
    if !any {
        return Ok((0, vec![(false, format!("→ not applicable: no Python files under {named} -- nothing for the formatter to have an opinion about"))]));
    }
    let args: Vec<&str> = trees.iter().map(String::as_str).collect();
    let (code, text) = ruff(root, &args, None)?;
    if code != 0 {
        let mut out: Lines = text.lines().map(|l| (true, format!("✗   {l}"))).collect();
        out.push((true, format!("✗ {named} is not ruff-formatted. Run your repo's fmt target (`ruff format`) — do not hand-fix the shape.")));
        return Ok((1, out));
    }
    Ok((
        0,
        vec![(
            false,
            format!("✓ every file under {named} is ruff-formatted"),
        )],
    ))
}

fn run(trees: &[String], staged_scope: bool) -> (i32, Lines) {
    if !on_path("ruff") {
        return (1, vec![(true, "✗ ruff is not installed, so nothing can check the shape of the Python here — `brew install ruff` (or `uv tool install ruff`). A missing formatter is a missing gate, not a pass.".to_owned())]);
    }
    let root = root();
    let trees: Vec<String> = if trees.is_empty() {
        vec![".".to_owned()]
    } else {
        trees.to_vec()
    };
    let result = if staged_scope {
        staged(&root).map(|lines| (i32::from(lines.iter().any(|(e, _)| *e)), lines))
    } else {
        full(&root, &trees)
    };
    result.unwrap_or_else(|e| (1, vec![(true, format!("✗ {e}"))]))
}

/// `goh python-formatted [TREES...] [--staged]`.
#[must_use]
pub fn run_command(trees: &[String], staged_scope: bool) -> i32 {
    let (code, lines) = run(trees, staged_scope);
    for (to_err, line) in lines {
        if to_err {
            eprintln!("{line}");
        } else {
            println!("{line}");
        }
    }
    code
}

/// The structural step (opt-in, `GOH_PYTHON_FORMATTED`); the delegated step's labels.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged_scope: bool) -> Option<i32> {
    if !crate::gatesrc::opt_in(cfg, "GOH_PYTHON_FORMATTED") {
        return None;
    }
    let label = if staged_scope {
        "python is ruff-formatted (staged)"
    } else {
        "python is ruff-formatted"
    };
    let start = crate::step_report::begin(label);
    match run(&[], staged_scope) {
        (0, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, lines) => {
            let text: String = lines.into_iter().map(|(_, l)| l + "\n").collect();
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported(
                    "crates/goh/src/pyformat.rs",
                    "check_python_formatted.py",
                ),
                &text,
                start,
            );
            Some(code)
        }
    }
}
