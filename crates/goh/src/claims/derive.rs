//! A marked claim -> the number the tree says, and the COMMAND that says it.
//! Port of the retired `checks/_claim_derive.py`.
//!
//! Four derivations, each a function of the tree: `lines` (the file-length
//! cap's own `line_count`), `tests` (test functions, `def test_*` at any
//! depth), `declared` (elements of ONE named module-level string list or
//! dict) and `files` (the length of `git ls-files`'s answer). Every refusal
//! is an `Err` carrying the reason, never a silent pass.

use std::path::Path;
use std::process::Command;

/// The printed command for a `tests` count.
const TEST_COUNT_CMD: &str = "python3 -c 'import ast,sys;print(sum(sum(1 for x in ast.walk(ast.parse(open(p).read())) if x.__class__.__name__.endswith(\"FunctionDef\") and x.name.startswith(\"test_\")) for p in sys.argv[1:]))'";
/// The printed command for a `declared` count.
const DECLARED_COUNT_CMD: &str = "python3 -c 'import ast,sys;b=ast.parse(open(sys.argv[1]).read()).body;a=[n for n in b if isinstance(n,ast.Assign) and any(getattr(t,\"id\",None)==sys.argv[2] for t in n.targets)];v=a[0].value;print(len(v.keys) if isinstance(v,ast.Dict) else len(v.elts))'";

/// `shlex.quote`.
#[must_use]
pub fn shlex_quote(s: &str) -> String {
    if s.is_empty() {
        return "''".to_owned();
    }
    let safe = s
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || "_@%+=:,./-".contains(c));
    if safe {
        s.to_owned()
    } else {
        format!("'{}'", s.replace('\'', "'\"'\"'"))
    }
}

fn git_ls(staged: bool, pathspec: &str, nul: bool) -> String {
    let z = if nul { " -z" } else { "" };
    if staged {
        return format!("git ls-files{z} --cached -- {}", shlex_quote(pathspec));
    }
    format!(
        "git ls-files{z} --cached --others --exclude-standard -- {}",
        shlex_quote(pathspec)
    )
}

/// Every path the tree this scope judges holds, matching `pathspec`.
///
/// `ls-files --cached` at `--staged` (what the commit will hold), plus
/// untracked-not-ignored otherwise. Run on a clean environment (contract 12).
///
/// # Errors
/// git could not list the tree.
pub fn tree_files(root: &Path, staged: bool, pathspec: &str) -> Result<Vec<String>, String> {
    let mut cmd = Command::new("git");
    cmd.arg("-C").arg(root).args(["ls-files", "-z", "--cached"]);
    if !staged {
        cmd.args(["--others", "--exclude-standard"]);
    }
    cmd.args(["--", pathspec]);
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("GIT_") {
            cmd.env_remove(key);
        }
    }
    let out = cmd
        .output()
        .map_err(|e| format!("git could not list the tree: {e}"))?;
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let last = stderr
            .trim()
            .lines()
            .last()
            .unwrap_or("no output")
            .to_owned();
        return Err(format!("git could not list the tree: {last}"));
    }
    let mut names: Vec<String> = out
        .stdout
        .split(|b| *b == 0)
        .filter(|n| !n.is_empty())
        .map(|n| String::from_utf8_lossy(n).into_owned())
        .collect();
    names.sort();
    Ok(names)
}

fn pathspec(directory: &str, extension: &str) -> String {
    if extension.is_empty() {
        format!("{directory}/*")
    } else {
        format!("{directory}/*.{}", extension.trim_start_matches('.'))
    }
}

fn check_target(target: &str) -> Result<(), String> {
    if target.is_empty() || target.starts_with('/') || target.starts_with('-') {
        return Err(format!("'{target}' is not a repo-relative path"));
    }
    if target.split('/').any(|p| p == "..") {
        return Err(format!("'{target}' points outside this repository"));
    }
    Ok(())
}

fn read(root: &Path, staged: bool, rel: &str) -> Result<Vec<u8>, String> {
    if staged && !tree_files(root, true, "*")?.iter().any(|f| f == rel) {
        return Err(format!(
            "{rel} is not in the index at --staged, so no claim about it can be re-derived"
        ));
    }
    crate::gitutil::content_bytes(root, rel, staged).ok_or_else(|| format!("{rel} cannot be read"))
}

fn is_file(root: &Path, staged: bool, target: &str) -> Result<bool, String> {
    check_target(target)?;
    if tree_files(root, staged, "*")?.iter().any(|f| f == target) {
        return Ok(true);
    }
    if !tree_files(root, staged, &format!("{target}/*"))?.is_empty() {
        return Ok(false);
    }
    if staged {
        return Err(format!(
            "{target} is not in the index at --staged, so no claim about it can be re-derived"
        ));
    }
    Err(format!("{target} names no path in this repo"))
}

fn parse(blob: &[u8], rel: &str) -> Result<crate::pylex::Lexed, String> {
    crate::pylex::lex(&String::from_utf8_lossy(blob))
        .ok_or_else(|| format!("{rel} does not parse as Python: the tokenizer refused it"))
}

fn count(n: usize) -> u64 {
    u64::try_from(n).unwrap_or(u64::MAX)
}

fn derive_lines(root: &Path, staged: bool, target: &str) -> Result<(u64, String), String> {
    if !is_file(root, staged, target)? {
        return Err(format!(
            "{target} is a directory; a `lines` claim names one file"
        ));
    }
    let blob = read(root, staged, target)?;
    Ok((
        count(crate::gitutil::line_count(&blob)),
        format!("awk 'END{{print NR}}' {}", shlex_quote(target)),
    ))
}

fn derive_tests(root: &Path, staged: bool, target: &str) -> Result<(u64, String), String> {
    let files = if is_file(root, staged, target)? {
        if super::text::suffix(target) != ".py" || !target.as_bytes().ends_with(b".py") {
            return Err(format!(
                "{target} is not a Python module; tests are counted by ast"
            ));
        }
        vec![target.to_owned()]
    } else {
        tree_files(root, staged, &pathspec(target, "py"))?
    };
    if files.is_empty() {
        return Err(format!("{target} holds no .py file to count tests in"));
    }
    let mut total = 0usize;
    for rel in &files {
        let lexed = parse(&read(root, staged, rel)?, rel)?;
        total += lexed
            .def_names()
            .iter()
            .filter(|n| n.starts_with("test_"))
            .count();
    }
    let command = if files.len() == 1 {
        format!("{TEST_COUNT_CMD} {}", shlex_quote(&files[0]))
    } else {
        format!(
            "{} | xargs -0 {TEST_COUNT_CMD}",
            git_ls(staged, &pathspec(target, "py"), true)
        )
    };
    Ok((count(total), command))
}

fn derive_declared(
    root: &Path,
    staged: bool,
    target: &str,
    name: &str,
) -> Result<(u64, String), String> {
    if !is_file(root, staged, target)? {
        return Err(format!(
            "{target} is a directory; name the file that declares the list"
        ));
    }
    let lists = parse(&read(root, staged, target)?, target)?.declared_lists();
    let names = || {
        let joined = lists.keys().cloned().collect::<Vec<_>>().join(", ");
        if joined.is_empty() {
            "none".to_owned()
        } else {
            joined
        }
    };
    let (n, resolved) = if name.is_empty() {
        if lists.len() != 1 {
            return Err(format!(
                    "{target} declares {} module-level string lists ({}), so the claim does not \
                     say which one is counted -- name it as PATH:NAME, or stop writing the number down",
                    lists.len(),
                    names()
                ));
        }
        let (only, n) = lists
            .iter()
            .next()
            .map(|(k, v)| (k.clone(), *v))
            .unwrap_or_default();
        (n, only)
    } else {
        let Some(n) = lists.get(name) else {
            return Err(format!(
                "{target} declares no module-level string list named '{name}' (it has {})",
                names()
            ));
        };
        (*n, name.to_owned())
    };
    Ok((
        count(n),
        format!(
            "{DECLARED_COUNT_CMD} {} {}",
            shlex_quote(target),
            shlex_quote(&resolved)
        ),
    ))
}

fn derive_files(
    root: &Path,
    staged: bool,
    target: &str,
    glob: Option<&str>,
) -> Result<(u64, String), String> {
    let Some(glob) = glob else {
        return Err("a `files` claim must name the extension it counts".to_owned());
    };
    if is_file(root, staged, target)? {
        return Err(format!(
            "{target} is a file; a `files` claim names the directory holding them"
        ));
    }
    let spec = pathspec(target, glob.trim_start_matches(['*', '.']));
    Ok((
        count(tree_files(root, staged, &spec)?.len()),
        format!("{} | wc -l", git_ls(staged, &spec, false)),
    ))
}

/// `(value, command)` for one claim.
///
/// # Errors
/// The claim cannot be re-derived, and why.
pub fn derive(
    root: &Path,
    staged: bool,
    kind: &str,
    target: &str,
    name: &str,
    glob: Option<&str>,
) -> Result<(u64, String), String> {
    match kind {
        "lines" => derive_lines(root, staged, target),
        "tests" => derive_tests(root, staged, target),
        "declared" => derive_declared(root, staged, target, name),
        "files" => derive_files(root, staged, target, glob),
        other => Err(format!("no derivation for '{other}'")),
    }
}

#[cfg(test)]
mod tests {
    use super::derive;

    /// The unit vocabulary is closed and every word in it HAS a derivation: a unit with no rule
    /// behind it is a promise nobody keeps, the class this gate is about. Four derivations, and
    /// `goh claim-derivation` re-derives the claim about that number wherever one is marked.
    #[test]
    fn every_unit_names_a_derivation_and_there_are_four() {
        let mut kinds: Vec<&str> = super::super::text::UNITS.iter().map(|(_, k)| *k).collect();
        kinds.sort_unstable();
        kinds.dedup();
        assert_eq!(kinds, ["declared", "files", "lines", "tests"]);
        let nowhere = std::path::Path::new("/nonexistent-goh-claims-root");
        for kind in kinds {
            let got = derive(nowhere, false, kind, "x", "", Some("*.py"));
            assert!(
                !got.as_ref().is_err_and(|e| e.starts_with("no derivation")),
                "{kind}: {got:?}"
            );
        }
        assert!(derive(nowhere, false, "widgets", "x", "", None)
            .is_err_and(|e| e.starts_with("no derivation")));
    }
}
