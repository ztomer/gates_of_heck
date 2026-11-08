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

/// The path a `# shellcheck source=` directive on this (trimmed) line names, if it is one.
fn directive_path(t: &str) -> Option<&str> {
    t.strip_prefix('#')?
        .trim_start()
        .strip_prefix("shellcheck")?
        .split_whitespace()
        .find_map(|w| w.strip_prefix("source="))
}

/// A source path's spelling in the tree shellcheck runs in: a literal path, or one led by ONE
/// variable (`$D/conf`, `${D}/conf`), which shellcheck follows with the variable stripped, from its
/// working directory (GOH.SHX2: a `.gatesrc` named only that way was never collected).
fn tree_path(p: &str) -> Option<String> {
    let p: String = p.chars().filter(|c| *c != '"' && *c != '\'').collect();
    let ident = |s: &str| !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
    let rest = if let Some(r) = p.strip_prefix("${") {
        let close = r.find('}')?;
        ident(&r[..close]).then_some(())?;
        r[close + 1..].strip_prefix('/')?
    } else if let Some(r) = p.strip_prefix('$') {
        let end = r
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_'))
            .unwrap_or(r.len());
        ident(&r[..end]).then_some(())?;
        r[end..].strip_prefix('/')?
    } else {
        p.as_str()
    };
    let rest = rest.trim_start_matches("./");
    (!rest.is_empty() && !rest.starts_with('/') && !rest.contains('$') && !rest.contains('`'))
        .then(|| rest.to_owned())
}

/// A source argument led by ONE command substitution -- the house idiom
/// `"$(dirname "${BASH_SOURCE[0]}")/lib.sh"` -- which shellcheck follows with the substitution
/// stripped (from its working directory, or the script's under `--source-path=SCRIPTDIR`): the rest.
fn subst_led(arg: &str) -> Option<String> {
    let body = arg.strip_prefix('"').unwrap_or(arg).strip_prefix("$(")?;
    let mut depth = 1usize;
    let mut close = None;
    for (i, c) in body.char_indices() {
        match c {
            '(' => depth += 1,
            ')' => {
                depth -= 1;
                if depth == 0 {
                    close = Some(i);
                    break;
                }
            }
            _ => {}
        }
    }
    let rest = body[close? + 1..]
        .trim_start_matches('"')
        .strip_prefix('/')?;
    let end = rest
        .find(|c: char| c.is_whitespace() || c == ';' || c == '|' || c == '&')
        .unwrap_or(rest.len());
    tree_path(&rest[..end])
}

/// The repo-relative paths a shell text names as a source it reads.
///
/// A `# shellcheck source=` directive, or a `source`/`.` path that is literal or led by one
/// variable ([`tree_path`]) or one command substitution ([`subst_led`]). `/dev/null` and absolute
/// paths name nothing in the tree.
#[must_use]
pub fn named_sources(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in text.lines() {
        let t = line.trim_start();
        let path = if t.starts_with('#') {
            directive_path(t).and_then(tree_path)
        } else {
            t.strip_prefix("source ")
                .or_else(|| t.strip_prefix(". "))
                .and_then(|r| {
                    subst_led(r.trim_start()).or_else(|| tree_path(r.split_whitespace().next()?))
                })
        };
        out.extend(path);
    }
    out
}

/// The repo-relative paths a shell file at `rel` may read as a source.
///
/// Each name [`named_sources`] finds, both as written (shellcheck's CWD, the repo root) and against
/// the script's own directory (`--source-path=SCRIPTDIR`, GOH.REL2), `..` resolved.
#[must_use]
pub fn sources_of(rel: &str, text: &str) -> Vec<String> {
    let dir = rel.rsplit_once('/').map_or("", |(d, _)| d);
    let mut out = Vec::new();
    for name in named_sources(text) {
        if !dir.is_empty() {
            let mut parts: Vec<&str> = dir.split('/').collect();
            let mut ok = true;
            for seg in name.split('/') {
                match seg {
                    "" | "." => {}
                    ".." => ok &= parts.pop().is_some(),
                    s => parts.push(s),
                }
            }
            if ok && !parts.is_empty() {
                out.push(parts.join("/"));
            }
        }
        out.push(name);
    }
    out
}

/// Each `# shellcheck source=` directive naming an ABSOLUTE path other than `/dev/null`.
///
/// `(line number, path)`. shellcheck reads that one machine's file: outside the tree, the staged
/// checkout and the verdict's key, and on every other machine nothing at all (GOH.SHX2).
#[must_use]
pub fn absolute_directives(text: &str) -> Vec<(usize, String)> {
    text.lines()
        .enumerate()
        .filter_map(|(i, line)| {
            let p = directive_path(line.trim_start())?.trim_matches(|c| c == '"' || c == '\'');
            (p.starts_with('/') && p != "/dev/null").then(|| (i + 1, p.to_owned()))
        })
        .collect()
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
    // The non-shell files a shell file names as a source (a `.gatesrc`), read from the index too,
    // and every tracked `.shellcheckrc`: shellcheck reads the one beside a script and its parents',
    // and a staged checkout without them judged a commit by rules the full scan does not use
    // (goh's `source-path=SCRIPTDIR` held at `--full` and vanished at commit, GOH.VENDOR).
    let named: BTreeSet<String> = shells
        .iter()
        .filter_map(|f| Some(sources_of(f, &std::fs::read_to_string(base.join(f)).ok()?)))
        .flatten()
        .collect();
    let extra: Vec<&String> = tracked
        .iter()
        .filter(|f| {
            !in_scope(f, None)
                && (named.contains(*f) || f.rsplit('/').next() == Some(".shellcheckrc"))
        })
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
            .iter()
            .flat_map(|(f, b)| sources_of(f, &String::from_utf8_lossy(b)))
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
    fn named_sources_reads_directives_literal_and_var_led_paths() {
        let text = "# shellcheck source=lib/a.sh\n. ./b.sh\nsource \"c.sh\"\n\
                    . \"$HERE/d.sh\"\n. \"${ROOT}\"/.gatesrc\n. \"$A/$B/e\"\n. \"$(x)/f\"\n\
                    # shellcheck source=/dev/null\nsource /etc/x\necho . x\n";
        assert_eq!(
            named_sources(text),
            vec!["lib/a.sh", "b.sh", "c.sh", "d.sh", ".gatesrc", "f"]
        );
    }

    #[test]
    fn a_dirname_led_source_is_read_with_the_substitution_stripped() {
        let text = ". \"$(dirname \"${BASH_SOURCE[0]}\")/lib.conf\"\n\
                    source \"$(cd \"$(dirname \"$0\")\" && pwd)\"/../x.sh || exit 1\n";
        assert_eq!(named_sources(text), vec!["lib.conf", "../x.sh"]);
    }

    #[test]
    fn sources_of_resolves_against_the_root_and_the_scripts_own_directory() {
        let text = ". ./a.sh\n. \"$(dirname \"$0\")/../lib/b.conf\"\n";
        assert_eq!(
            super::sources_of("tools/x/run.sh", text),
            vec!["tools/x/a.sh", "a.sh", "tools/lib/b.conf", "../lib/b.conf"]
        );
        assert_eq!(
            super::sources_of("run.sh", ". ../up.sh\n"),
            vec!["../up.sh"]
        );
    }

    #[test]
    fn absolute_directives_name_every_machine_path_but_dev_null() {
        let text = "# shellcheck source=/Users/x/lib.sh\n# shellcheck source=/dev/null\n\
                    # shellcheck source=lib.sh\n  # shellcheck disable=SC1 source=\"/opt/y\"\n";
        assert_eq!(
            super::absolute_directives(text),
            vec![(1, "/Users/x/lib.sh".to_owned()), (4, "/opt/y".to_owned())]
        );
    }
}
