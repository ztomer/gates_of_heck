//! The release gate's `cargo fmt --all -- --check`, at COMMIT time, over the STAGED `.rs` blobs.
//!
//! `app_updates` `c0d3a57` (G1e, 2026-10-25): the commit hook passed a TEST file that the release
//! gate's `cargo fmt --check` refused. The commit-time Rust layer judged policy (`#[allow]`,
//! emptiness asserts) and never shape, so it was weaker than the gate it feeds -- the class
//! `steps_rust` was written for, one knob over.
//!
//! Each staged `.rs` that belongs to a package is judged the way `cargo fmt` judges it:
//!
//!   * the STAGED blob, on rustfmt's stdin (the commit carries the index, not the disk);
//!   * the PACKAGE's edition, passed as `--edition`, as cargo fmt passes it -- rustfmt's own
//!     default is 2015, under which `async fn` does not parse -- and inherited from
//!     `[workspace.package]` when the manifest says `edition.workspace = true`;
//!   * the nearest `rustfmt.toml` / `.rustfmt.toml` between the file and the repo root, as
//!     `--config-path`;
//!   * from the repo root, so a `rust-toolchain.toml` picks the rustfmt the release gate runs.
//!
//! A file in no package is not judged: `cargo fmt` would not reach it either. A missing rustfmt is
//! a missing gate, not a pass.

use std::fmt::Write as _;
use std::path::Path;
use std::process::Command;

/// What a staged file's judgement came to.
enum Verdict {
    Clean,
    Misformatted(String),
    NotInAPackage,
}

fn manifest(repo: &Path, rel: &str) -> Option<toml::Table> {
    let bytes = crate::gitutil::content_bytes(repo, rel, true)
        .or_else(|| std::fs::read(repo.join(rel)).ok())?;
    String::from_utf8_lossy(&bytes).parse::<toml::Table>().ok()
}

/// The directories from `rel`'s own up to the repo root, nearest first, as repo-relative prefixes
/// (`""` is the root).
fn ancestors(rel: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut dir = Path::new(rel).parent();
    while let Some(d) = dir {
        out.push(d.to_string_lossy().into_owned());
        if d.as_os_str().is_empty() {
            break;
        }
        dir = d.parent();
    }
    out
}

fn join(dir: &str, name: &str) -> String {
    if dir.is_empty() {
        name.to_owned()
    } else {
        format!("{dir}/{name}")
    }
}

fn exists(repo: &Path, rel: &str) -> bool {
    crate::gitutil::content_bytes(repo, rel, true).is_some() || repo.join(rel).is_file()
}

/// The edition `cargo fmt` would give `rel`, or None when no package owns it.
fn edition(repo: &Path, rel: &str) -> Option<String> {
    let dirs = ancestors(rel);
    let mut inherit = false;
    for dir in &dirs {
        let Some(table) = manifest(repo, &join(dir, "Cargo.toml")) else {
            continue;
        };
        if !inherit {
            let Some(package) = table.get("package").and_then(toml::Value::as_table) else {
                continue;
            };
            match package.get("edition") {
                Some(toml::Value::String(e)) => return Some(e.clone()),
                Some(toml::Value::Table(t))
                    if t.get("workspace").and_then(toml::Value::as_bool) == Some(true) =>
                {
                    inherit = true;
                    // The workspace root may be this very manifest.
                }
                _ => return Some("2015".to_owned()),
            }
        }
        if inherit {
            if let Some(e) = table
                .get("workspace")
                .and_then(|w| w.get("package"))
                .and_then(|p| p.get("edition"))
                .and_then(toml::Value::as_str)
            {
                return Some(e.to_owned());
            }
        }
    }
    // An inherited edition with no workspace to inherit from is a manifest cargo refuses; the
    // file is still in a package, judged at cargo's default.
    inherit.then(|| "2015".to_owned())
}

fn config(repo: &Path, rel: &str) -> Option<String> {
    ancestors(rel).iter().find_map(|dir| {
        ["rustfmt.toml", ".rustfmt.toml"]
            .iter()
            .map(|name| join(dir, name))
            .find(|candidate| repo.join(candidate).is_file() || exists(repo, candidate))
    })
}

fn judge(repo: &Path, rel: &str) -> Result<Verdict, String> {
    let Some(edition) = edition(repo, rel) else {
        return Ok(Verdict::NotInAPackage);
    };
    let Some(blob) = crate::gitutil::content_bytes(repo, rel, true) else {
        return Ok(Verdict::Clean); // deleted in this commit: nothing to judge
    };
    let mut cmd = Command::new("rustfmt");
    // `--emit stdout` and a byte comparison, not `--check`: MEASURED on rustfmt 1.10.0
    // (2026-10-25), `--check` on stdin prints the diff and EXITS 0, so its status says nothing.
    cmd.args([
        "--emit",
        "stdout",
        "--color",
        "never",
        "--edition",
        &edition,
    ])
    .current_dir(repo);
    let scratch;
    if let Some(cfg) = config(repo, rel) {
        // The staged rustfmt.toml, written beside nothing it could change: rustfmt reads a path.
        let bytes = crate::gitutil::content_bytes(repo, &cfg, true)
            .or_else(|| std::fs::read(repo.join(&cfg)).ok())
            .unwrap_or_default();
        scratch = tempfile_for(&bytes).map_err(|e| format!("cannot stage {cfg}: {e}"))?;
        cmd.arg("--config-path").arg(scratch.path());
    }
    let ceiling = crate::bounded::step_ceiling();
    let ran = crate::bounded::run(&mut cmd, Some(blob.clone()), ceiling)
        .map_err(|e| format!("cannot start rustfmt: {e}"))?;
    if ran.timed_out {
        return Err(format!(
            "TIMED OUT after {}s: rustfmt {rel}",
            ceiling.as_secs()
        ));
    }
    match ran.code {
        Some(0) if ran.stdout == blob => Ok(Verdict::Clean),
        Some(0) => Ok(Verdict::Misformatted(first_difference(&blob, &ran.stdout))),
        code => Err(format!(
            "rustfmt could not judge {rel} (exit {code:?}) -- `cargo fmt --check` refuses it too: {}",
            String::from_utf8_lossy(&ran.stderr).trim()
        )),
    }
}

/// Where the staged text and rustfmt's text first part: the line, as staged and as formatted.
fn first_difference(staged: &[u8], formatted: &[u8]) -> String {
    let staged = String::from_utf8_lossy(staged);
    let formatted = String::from_utf8_lossy(formatted);
    let mut a = staged.lines();
    let mut b = formatted.lines();
    let mut line = 1usize;
    loop {
        match (a.next(), b.next()) {
            (Some(x), Some(y)) if x == y => line += 1,
            (x, y) => {
                return format!(
                    "line {line}: staged {:?}, rustfmt {:?}",
                    x.unwrap_or("<end>"),
                    y.unwrap_or("<end>")
                );
            }
        }
    }
}

/// A file holding `bytes`, removed when dropped.
struct Scratch(std::path::PathBuf);

impl Scratch {
    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

fn tempfile_for(bytes: &[u8]) -> std::io::Result<Scratch> {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static NEXT: AtomicUsize = AtomicUsize::new(0);
    let n = NEXT.fetch_add(1, Ordering::Relaxed);
    let path = std::env::temp_dir().join(format!("goh-rustfmt-{}-{n}.toml", std::process::id()));
    std::fs::write(&path, bytes)?;
    Ok(Scratch(path))
}

/// The staged `.rs` files that are not rustfmt-clean, as a report; `Ok(None)` when every one is.
///
/// # Errors
/// rustfmt is missing, cannot start, or cannot judge a file.
pub fn check(repo: &Path, files: &[&String]) -> Result<Option<String>, String> {
    if !crate::pyformat::on_path("rustfmt") {
        return Err(
            "rustfmt is not installed, so nothing can check the shape of the staged Rust -- \
             `rustup component add rustfmt`. A missing formatter is a missing gate, not a pass."
                .to_owned(),
        );
    }
    // Up to eight rustfmts at once, each spawned before any is joined.
    let results: Vec<Result<(String, Verdict), String>> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for chunk in files.chunks(files.len().div_ceil(8).max(1)) {
            handles.push(scope.spawn(move || {
                chunk
                    .iter()
                    .map(|rel| judge(repo, rel).map(|v| ((*rel).clone(), v)))
                    .collect::<Vec<_>>()
            }));
        }
        let mut all = Vec::new();
        for h in handles {
            all.extend(h.join().unwrap_or_default());
        }
        all
    });
    let mut bad = Vec::new();
    let mut judged = 0usize;
    for result in results {
        match result? {
            (_, Verdict::NotInAPackage) => {}
            (_, Verdict::Clean) => judged += 1,
            (rel, Verdict::Misformatted(diff)) => {
                judged += 1;
                bad.push((rel, diff));
            }
        }
    }
    if bad.is_empty() {
        return Ok(None);
    }
    let mut report = String::new();
    for (rel, diff) in &bad {
        let _ = writeln!(report, "  {rel}: not rustfmt-clean");
        for line in diff.lines().take(12) {
            let _ = writeln!(report, "      {line}");
        }
    }
    let _ = writeln!(
        report,
        "{} of {judged} staged Rust file(s) are not rustfmt-clean, which the release gate's \
         `cargo fmt --all -- --check` refuses. Run `cargo fmt` and re-stage -- do not hand-fix \
         the shape.",
        bad.len()
    );
    Ok(Some(report))
}
