//! The shell lint's SUBJECT: which files it judges, the tree shellcheck reads them in, and what
//! each verdict depends on besides the file itself.
//!
//! The lint runs `shellcheck -x` (GOH.SHX): a script is judged together with what it `source`s,
//! because that is where shellcheck's data flow sees a sourced array reused as a string (SC2178,
//! SC2128) or a variable a library assigns (SC2154). Without `-x` the gate printed clean over
//! `tools/release-kit/release.sh` while `shellcheck -x` named four warnings in it -- and it had
//! been blind since GOH.SHSEV split the one batched call into one call per file, because a batch
//! followed sources only between files that happened to share it.
//!
//! Following sources makes the verdict depend on OTHER files, so two things follow here:
//!   * the TREE: shellcheck resolves a `source` path (and a `# shellcheck source=` directive)
//!     against its working directory, so it runs at the repo root at `--full`, and at `--staged`
//!     in a checkout of the INDEX -- every tracked shell file and every tracked file a shell file
//!     names as a source, so a staged script reads its library as it is being committed;
//!   * the KEY: a verdict is reused only while every file it may have read is unchanged. That set
//!     is over-approximated by name: a file depends on every universe file whose name it mentions,
//!     transitively. Over-approximation costs a re-run; under-approximation would reuse a stale
//!     verdict, so it errs toward the first.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::process::Command;

/// `(display path, path to read)` for each shell file in scope.
pub type Files = Vec<(String, PathBuf)>;

/// `git -C root <args>`'s NUL-separated output.
///
/// # Errors
/// A git that cannot run or refuses (a corrupt index): never read as no files.
pub fn git_z(root: &Path, args: &[&str]) -> Result<Vec<String>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|e| format!("git: {e}"))?;
    // A git that refuses (a corrupt index) lists nothing; read as no shell files, that passed.
    if !out.status.success() {
        return Err(format!(
            "git {} failed: {}",
            args.first().copied().unwrap_or(""),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(out
        .stdout
        .split(|b| *b == 0)
        .filter(|n| !n.is_empty())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .collect())
}

/// A `*.sh` file, or an EXTENSIONLESS file under `hooks/`.
///
/// A git hook is named `pre-commit`, not `pre-commit.sh`. An extension that names another language is that language: a Python hook in
/// `hooks/claude/` was failed by `bash -n` on its first parenthesis.
#[must_use]
pub fn in_scope(f: &str, exclude: Option<&crate::pathfilter::PathFilter>) -> bool {
    let name = f.rsplit('/').next().unwrap_or(f);
    (f.as_bytes().ends_with(b".sh") || (f.starts_with("hooks/") && !name.contains('.')))
        && !exclude.is_some_and(|x| x.is_match(f))
}

/// The repo-relative paths a shell text names as a source it reads.
///
/// A `# shellcheck source=` directive, or a literal `source`/`.` path with no expansion in it. `/dev/null` and absolute
/// paths name nothing in the tree.
#[must_use]
pub fn named_sources(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in text.lines() {
        let t = line.trim_start();
        let path = t.strip_prefix('#').map_or_else(
            || {
                t.strip_prefix("source ")
                    .or_else(|| t.strip_prefix(". "))
                    .and_then(|r| r.split_whitespace().next())
            },
            |rest| {
                rest.trim_start()
                    .strip_prefix("shellcheck")
                    .and_then(|r| r.split_whitespace().find_map(|w| w.strip_prefix("source=")))
            },
        );
        let Some(p) = path.map(|p| p.trim_matches(|c| c == '"' || c == '\'')) else {
            continue;
        };
        if p.is_empty() || p.starts_with('/') || p.contains('$') || p.contains('`') {
            continue;
        }
        out.push(p.trim_start_matches("./").to_owned());
    }
    out
}

/// A temp dir removed when dropped.
pub struct TempDir(pub PathBuf);

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

/// The judged files, the tree shellcheck runs in, the dependency key per file, and the temp dir a
/// staged index was checked out into (held until the judgement is done).
pub struct Scope {
    pub files: Files,
    pub base: PathBuf,
    pub deps: Deps,
    pub tmp: Option<TempDir>,
}

fn checkout_index(root: &Path, prefix: &str, paths: &[&String]) {
    if paths.is_empty() {
        return;
    }
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(root).args([
        "checkout-index",
        "-z",
        "--stdin",
        &format!("--prefix={prefix}"),
    ]);
    let list: Vec<u8> = paths.iter().flat_map(|f| f.bytes().chain([0])).collect();
    let _ = crate::bounded::run(&mut cmd, Some(list), crate::bounded::step_ceiling());
}

/// The shell files in scope and the tree they are read in (see the module doc).
///
/// # Errors
/// A git listing that fails, or no temp dir for the staged index.
pub fn gather(
    root: &Path,
    staged: bool,
    exclude: Option<&crate::pathfilter::PathFilter>,
) -> Result<Scope, String> {
    if !staged {
        let tracked = git_z(root, &["ls-files", "-z"])?;
        let shells: Vec<&String> = tracked.iter().filter(|f| in_scope(f, None)).collect();
        let files = shells
            .iter()
            .filter(|f| in_scope(f, exclude))
            .map(|f| ((*f).clone(), root.join(f)))
            .collect();
        let deps = Deps::build(root, &tracked, &shells);
        return Ok(Scope {
            files,
            base: root.to_path_buf(),
            deps,
            tmp: None,
        });
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
        let deps = Deps::default();
        return Ok(Scope {
            files: Vec::new(),
            base: root.to_path_buf(),
            deps,
            tmp: None,
        });
    }
    // Listed only once something is staged: a commit with no shell file asks git nothing more
    // (tests/test_goh_git_spawns.py is the ratchet).
    let tracked = git_z(root, &["ls-files", "-z"])?;
    let shells: Vec<&String> = tracked.iter().filter(|f| in_scope(f, None)).collect();
    let tmp = temp_dir()?;
    let base = tmp.0.join("index");
    let prefix = format!("{}/", base.display());
    checkout_index(root, &prefix, &shells);
    // The non-shell files a shell file names as a source (a `.gatesrc`), read from the index too.
    let named: BTreeSet<String> = shells
        .iter()
        .filter_map(|f| std::fs::read_to_string(base.join(f)).ok())
        .flat_map(|t| named_sources(&t))
        .collect();
    let extra: Vec<&String> = tracked
        .iter()
        .filter(|f| !in_scope(f, None) && named.contains(*f))
        .collect();
    checkout_index(root, &prefix, &extra);
    let files = picked
        .into_iter()
        .filter_map(|f| {
            let src = base.join(&f);
            src.is_file().then_some((f, src))
        })
        .collect();
    let deps = Deps::build(&base, &tracked, &shells);
    Ok(Scope {
        files,
        base,
        deps,
        tmp: Some(tmp),
    })
}

/// What each file's verdict may read: every universe file (tracked shell files and the tracked
/// files they name as sources) reachable by name mention, with its content hash.
#[derive(Default)]
pub struct Deps {
    hashes: BTreeMap<String, String>,
    edges: BTreeMap<String, Vec<String>>,
}

impl Deps {
    fn build(base: &Path, tracked: &[String], shells: &[&String]) -> Self {
        let mut texts: BTreeMap<String, Vec<u8>> = BTreeMap::new();
        for f in shells {
            if let Ok(b) = std::fs::read(base.join(f)) {
                texts.insert((*f).clone(), b);
            }
        }
        let named: BTreeSet<String> = texts
            .values()
            .flat_map(|b| named_sources(&String::from_utf8_lossy(b)))
            .collect();
        for f in tracked.iter().filter(|f| named.contains(*f)) {
            if texts.contains_key(f) {
                continue;
            }
            if let Ok(b) = std::fs::read(base.join(f)) {
                texts.insert(f.clone(), b);
            }
        }
        let basenames: Vec<(&String, String)> = texts
            .keys()
            .map(|p| (p, p.rsplit('/').next().unwrap_or(p).to_owned()))
            .collect();
        let mut edges = BTreeMap::new();
        for (path, bytes) in &texts {
            let text = String::from_utf8_lossy(bytes);
            let to: Vec<String> = basenames
                .iter()
                .filter(|(p, n)| *p != path && text.contains(n.as_str()))
                .map(|(p, _)| (*p).clone())
                .collect();
            edges.insert(path.clone(), to);
        }
        let hashes = texts
            .iter()
            .map(|(p, b)| (p.clone(), crate::verdict_cache::content_hash(b)))
            .collect();
        Self { hashes, edges }
    }

    /// `display` and every file reachable from it, each with its content hash: the part of a
    /// verdict's key that says what shellcheck could have read. `None` when `display` was not read.
    #[must_use]
    pub fn key(&self, display: &str) -> Option<String> {
        self.hashes.get(display)?;
        let mut seen: BTreeSet<&str> = BTreeSet::new();
        let mut todo = vec![display];
        while let Some(f) = todo.pop() {
            if seen.insert(f) {
                todo.extend(self.edges.get(f).into_iter().flatten().map(String::as_str));
            }
        }
        Some(
            seen.iter()
                .map(|f| format!("{f}={}", self.hashes.get(*f).map_or("-", String::as_str)))
                .collect::<Vec<_>>()
                .join(","),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::named_sources;

    #[test]
    fn named_sources_reads_directives_and_literal_paths_only() {
        let text = "# shellcheck source=lib/a.sh\n. ./b.sh\nsource \"c.sh\"\n\
                    . \"$HERE/d.sh\"\n# shellcheck source=/dev/null\nsource /etc/x\necho . x\n";
        assert_eq!(named_sources(text), vec!["lib/a.sh", "b.sh", "c.sh"]);
    }
}
