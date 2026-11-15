//! A git process stopped at a deadline strands no lock, and a reader takes none
//! (`goh git-deadline`, GOH.GITKILLNATIVE; the class is GOH.GITKILL's).
//!
//! THE CLASS, found in `.loop` on 2026-11-08 (`4e82af6`, `7ae11d8`): `subprocess.run(["git", ...],
//! timeout=T)` SIGKILLs git ALONE at the deadline. A writer (commit, add, merge) dies holding
//! `index.lock` and orphans its hook, so every later commit in the repo is refused; a work-tree
//! reader (`git status`, `git diff` without `--cached`) takes the same lock OPTIONALLY to refresh
//! the index, and dies holding it just the same. `gates_of_heck` fixed its own sites (v0.80.0) with
//! a pytest scan of its own tree, so the consumer that had the same shape (`.loop`'s `loopq.py`)
//! got no check. This is that scan, native, a structural step in every repo.
//!
//! WHAT IS JUDGED (comments and docstrings are never read):
//!
//! * Python, one logical statement at a time (`crate::callkey::statements`):
//!   - `subprocess.{run,Popen,check_output,call,check_call}(["git", ...], ..., timeout=...)`,
//!     unless the statement runs git with no optional lock (`GIT_OPTIONAL_LOCKS`,
//!     `--no-optional-locks`, a `reader_env`) and its verb is no writer's: a reader with no lock
//!     to strand may be stopped at a deadline; a writer may not, whatever its environment;
//!   - a literal `["git", ..., "status"|"diff", ...]` that reads the work tree, whose statement
//!     does not name the no-lock setting.
//! * Shell, one line at a time (`crate::shellsrc`): a `git status`, or a `git diff` that reads the
//!   work tree, without `GIT_OPTIONAL_LOCKS=0` or `--no-optional-locks`, and a git under
//!   `timeout`/`gtimeout` (git alone is stopped; its hooks are orphaned).
//!
//! The verb is git's verb, a word (`crate::gitargv`), never a substring of the line: a commit
//! message saying "diff" or a plumbing `diff-tree` is no reader. A `git diff` reads the work tree
//! unless it says `--cached`/`--staged`/`--no-index`, names a range (`A..B`), or names two operands
//! before `--` (two revisions: `koffee_big`'s `git diff --quiet "v$VERSION" HEAD` took no lock and
//! was the false positive that found this).
//!
//! Test files are not read (`tests/`, `test_*.py`, `*_test.py`, `conftest.py`): a test stops git
//! in a scratch repository on purpose. A site that is deliberate says so on one of its lines:
//! `# git-deadline-ok: <reason>` (the seam that every other call goes through, say).
//!
//! THE FIX: a writer under a deadline runs in its own process group and gets SIGTERM first (git's
//! handler removes its lock files; its hooks die with the group), then SIGKILL after a grace; a
//! reader runs with `GIT_OPTIONAL_LOCKS=0`, so there is no lock to strand (`gates_of_heck`'s
//! `lib/gitrun.py` `run_writer` and `run_reader`).
//!
//! WIRING: a structural step in EVERY repo, judged from `crate::grace::OVER_FROM` (a week for the
//! estate to fix or seed its sites; before it the scan reads and reports nothing). The sites a
//! consumer keeps are seeded in `git_deadline_seed.json` (`crate::estate_seed`): seeded files
//! warn, anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use crate::gitargv::{invocation, python_argv, shell_invocations, takes_optional_lock, WRITERS};
use regex::Regex;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[git_deadline]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no git stopped at a deadline holding a lock";
/// The escape a deliberate site carries on one of its lines, with its reason.
pub const ESCAPE: &str = "git-deadline-ok:";

/// The seed of the sites a consumer keeps.
pub const KIND: Kind = Kind {
    file: "git_deadline_seed.json",
    unit: "git deadline site",
    name: "git-deadline seed",
};

const FIX: &str = "    subprocess's timeout= (and the timeout command) stops git ALONE: a writer dies holding\n    \
     index.lock and orphans its hook, so every later commit is refused; git status/diff take the\n    \
     same lock optionally and strand it just the same (GOH.GITKILL). A reader: run it with\n    \
     GIT_OPTIONAL_LOCKS=0 (or git --no-optional-locks). A writer under a deadline: its own process\n    \
     group, SIGTERM first (git removes its locks), SIGKILL after a grace (gates_of_heck\n    \
     lib/gitrun.py run_writer). Deliberate: `# git-deadline-ok: <reason>` on the site.\n";

fn rx(p: &str) -> Regex {
    Regex::new(p).unwrap_or_else(|e| unreachable!("a constant pattern: {e}"))
}

static PY_SPAWN_GIT: LazyLock<Regex> = LazyLock::new(|| {
    rx(r#"\bsubprocess\.(?:run|Popen|check_output|call|check_call)\(\s*[\[(]\s*[rRuU]?["']git["']"#)
});
static PY_DEADLINE: LazyLock<Regex> = LazyLock::new(|| rx(r"\btimeout\s*=\s*[^N\s)]"));
static NO_LOCK: LazyLock<Regex> =
    LazyLock::new(|| rx(r"OPTIONAL_LOCKS|no-optional-locks|reader_env"));
static SH_TIMEOUT: LazyLock<Regex> = LazyLock::new(|| rx(r"\bg?timeout\b[^|;&]*\bgit\b"));

/// Why one Python statement stops git at a deadline or reads the work tree with the lock.
#[must_use]
pub fn python_finding(stmt: &str) -> Option<&'static str> {
    let m = PY_SPAWN_GIT.find(stmt)?;
    let open = stmt[..m.end()].rfind(['[', '(']).unwrap_or(0);
    let argv = python_argv(&stmt[open + 1..]);
    let inv = invocation(argv.get(1..).unwrap_or_default());
    let no_lock = NO_LOCK.is_match(stmt);
    let writer = inv.verb.as_deref().is_some_and(|v| WRITERS.contains(&v));
    if PY_DEADLINE.is_match(stmt) && (!no_lock || writer) {
        return Some("git under subprocess's timeout=: SIGKILLed alone at the deadline");
    }
    (takes_optional_lock(&inv) && !no_lock)
        .then_some("git status/diff without GIT_OPTIONAL_LOCKS=0: takes index.lock")
}

/// Why one shell line stops git at a deadline or reads the work tree with the lock.
#[must_use]
pub fn shell_finding(line: &str) -> Option<&'static str> {
    if line.trim_start().starts_with('#') {
        return None;
    }
    let code = line.split("  #").next().unwrap_or(line);
    if SH_TIMEOUT.is_match(code) {
        return Some("git under timeout: stopped alone, its hooks orphaned");
    }
    (!code.contains("GIT_OPTIONAL_LOCKS=0")
        && shell_invocations(code).iter().any(takes_optional_lock))
    .then_some("git status/diff without GIT_OPTIONAL_LOCKS=0: takes index.lock")
}

/// `(first line, why)` of every Python statement in `text` that is a finding; `None` when the
/// file does not lex.
#[must_use]
pub fn scan_python(text: &str) -> Option<Vec<(usize, &'static str)>> {
    let stmts = crate::callkey::statements(text)?;
    let prose = crate::pylex::lex(text)?.bare_strings(); // a docstring runs nothing
    let raw: Vec<&str> = text.split('\n').collect();
    // Every line of one statement maps to the same text: group the runs, judge each once.
    let mut runs: Vec<(usize, usize, &str)> = Vec::new();
    for (&n, s) in &stmts {
        match runs.last_mut() {
            Some(run) if run.2 == s.as_str() && run.1 + 1 == n => run.1 = n,
            _ => runs.push((n, n, s.as_str())),
        }
    }
    let escaped = |a: usize, b: usize| {
        raw.get(a - 1..b)
            .is_some_and(|ls| ls.iter().any(|l| l.contains(ESCAPE)))
    };
    Some(
        runs.into_iter()
            .filter_map(|(first, last, s)| {
                let why = python_finding(s)?;
                let is_prose = prose.iter().any(|&(a, b)| a <= first && last <= b);
                (!is_prose && !escaped(first, last)).then_some((first, why))
            })
            .collect(),
    )
}

/// `(line, why)` of every shell line in `text` that is a finding.
#[must_use]
pub fn scan_shell(text: &str) -> Vec<(usize, &'static str)> {
    text.lines()
        .enumerate()
        .filter(|(_, l)| !l.contains(ESCAPE))
        .filter_map(|(i, l)| shell_finding(l).map(|why| (i + 1, why)))
        .collect()
}

/// A test file: it stops git in a scratch repository on purpose.
#[must_use]
pub fn is_test(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    let stem = name.strip_suffix(".py").unwrap_or("");
    rel.starts_with("tests/")
        || rel.starts_with("test/")
        || rel.contains("/tests/")
        || rel.contains("/test/")
        || name == "conftest.py"
        || stem.starts_with("test_")
        || stem.ends_with("_test")
}

fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let files: Vec<String> = files.iter().filter(|f| !is_test(f)).cloned().collect();
    let shell = crate::shellsrc::sources(root, &files, exclude.as_ref(), staged);
    let python = crate::bareopen_py::sources(root, &files, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::GIT_DEADLINE, shell.len() + python.len());
    let mut out = Sites::new();
    if !crate::grace::over() {
        return Ok(out); // read, so the floor counts them; judged from the grace's end
    }
    let mut add = |rel: &String, text: &str, hits: Vec<(usize, &str)>| {
        let rows: Vec<&str> = text.lines().collect();
        let found: Vec<String> = hits
            .into_iter()
            .map(|(n, why)| {
                let line = rows.get(n - 1).copied().unwrap_or("");
                format!("    {rel}:{n}: {}  -- {why}", estate_seed::clip(line))
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    };
    for (rel, text) in &python {
        add(rel, text, scan_python(text).unwrap_or_default());
    }
    for (rel, text) in &shell {
        add(rel, text, scan_shell(text));
    }
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let report = Report {
        tag: TAG,
        item: "GOH.GITKILLNATIVE",
        what: (
            "git call that can strand index.lock",
            "git calls that can strand index.lock",
        ),
        fix: FIX,
        ok: "no git is stopped at a deadline holding a lock".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh git-deadline [--staged] [--exclude RE]`.
#[must_use]
pub fn run_command(staged: bool, exclude: &str) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{} {TAG} not a git repository", goh_tui::ERR);
        return 2;
    };
    match crate::gitutil::listed_files(&root, staged) {
        Ok(files) => estate_seed::print(judge(&root, &files, exclude, staged)),
        Err(m) => {
            goh_tui::eoutln!("{} {TAG} {m}", goh_tui::ERR);
            2
        }
    }
}

/// The structural step, in every repo.
#[must_use]
pub fn step(
    repo: &Path,
    files: &[String],
    cfg: &crate::gatesrc::Gatesrc,
    staged: bool,
) -> Option<i32> {
    estate_seed::step(
        LABEL,
        "crates/goh/src/gitdeadline.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_git_under_a_bare_deadline_is_found() {
        for bad in [
            "subprocess.run([\"git\", \"commit\", \"-q\"], timeout=5)\n",
            "subprocess.run(\n    [\"git\", \"status\"],\n    cwd=r,\n    timeout=T,\n)\n",
            "out = subprocess.check_output(['git', 'log'], timeout=3)\n",
            // a no-lock env does not make a writer safe to SIGKILL
            "subprocess.run(['git', 'commit'], env=reader_env(), timeout=5)\n",
        ] {
            let hits = scan_python(bad).expect("lexes");
            assert_eq!(hits.len(), 1, "{bad}");
            assert!(hits[0].1.contains("timeout="), "{bad}");
        }
    }

    #[test]
    fn a_work_tree_reader_without_the_no_lock_env_is_found() {
        let hits =
            scan_python("subprocess.run([\"git\", \"diff\", \"--name-only\"])\n").expect("lexes");
        assert_eq!(
            hits,
            vec![(
                1,
                "git status/diff without GIT_OPTIONAL_LOCKS=0: takes index.lock"
            )]
        );
    }

    #[test]
    fn the_safe_and_unrelated_forms_pass() {
        for ok in [
            "subprocess.run(['git', 'status'], env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'}, timeout=5)\n",
            "subprocess.run(['git', *args], env=reader_env(env), timeout=t)\n",
            "subprocess.run(['git', 'diff', '--cached'], timeout=None)\n",
            "subprocess.run(['git', 'log'])\n",
            "subprocess.run(['echo', 'status'], timeout=5)\n",
            "# subprocess.run(['git', 'commit'], timeout=5)\n",
            "\"\"\"subprocess.run(['git', 'commit'], timeout=5)\"\"\"\n",
            "subprocess.run(  # git-deadline-ok: the seam\n    ['git', *a], timeout=t, env=e)\n",
        ] {
            assert_eq!(scan_python(ok).expect("lexes"), vec![], "{ok}");
        }
    }

    #[test]
    fn the_shell_forms() {
        assert_eq!(scan_shell("git status --short\n").len(), 1);
        assert_eq!(scan_shell("x=$(git -C \"$r\" diff --name-only)\n").len(), 1);
        assert_eq!(scan_shell("timeout 30 git fetch\n").len(), 1);
        for ok in [
            "GIT_OPTIONAL_LOCKS=0 git status --porcelain\n",
            "git --no-optional-locks diff\n",
            "git diff --cached --quiet\n",
            "# git status\n",
            "git log -1  # then git status\n",
            "git status  # git-deadline-ok: interactive, never stopped\n",
        ] {
            assert!(scan_shell(ok).is_empty(), "{ok}");
        }
    }

    #[test]
    fn a_diff_that_reads_no_work_tree_is_not_a_reader() {
        // koffee_big tools/cut_release.sh:274 and :360: two revisions take no index lock, nor does
        // a range, --no-index, or a plumbing diff-*; nor is a `diff` that is not git's verb.
        for ok in [
            "        done < <(git diff --name-only \"$START_SHA\" HEAD)\n",
            "if git diff --quiet \"v$VERSION\" HEAD; then\n",
            "git diff --stat HEAD~3..HEAD\n",
            "git diff \"$base\"...\"$head\" -- src\n",
            "git diff --no-index a.txt b.txt\n",
            "git diff-tree --no-commit-id -r HEAD\n",
            "git diff-index --quiet HEAD --\n",
            "git commit -m \"fix the diff and the status line\"\n",
            "git log --grep=status --oneline\n",
        ] {
            assert!(scan_shell(ok).is_empty(), "{ok}");
        }
        for bad in [
            "git diff HEAD\n",
            "git diff --quiet HEAD -- src\n",
            "git -C \"$r\" diff\n",
            "out=\"$(git -c core.quotepath=off status --porcelain)\"\n",
            "git log -1 && git diff --stat\n",
        ] {
            assert_eq!(scan_shell(bad).len(), 1, "{bad}");
        }
        for ok in [
            "subprocess.run(['git', 'diff', '--quiet', base, 'HEAD'])\n",
            "subprocess.run(['git', 'diff', 'v1..v2'])\n",
            "subprocess.run(['git', 'diff', '--no-index', a, b])\n",
            "subprocess.run(['git', 'diff-tree', '-r', sha])\n",
            "subprocess.run(['git', 'commit', '-m', 'diff'])\n",
            "subprocess.run(['git', 'log', '--grep', 'add'], env=reader_env(), timeout=5)\n",
        ] {
            assert_eq!(scan_python(ok).expect("lexes"), vec![], "{ok}");
        }
        for bad in [
            "subprocess.run(['git', 'diff', 'HEAD'])\n",
            "subprocess.run(['git', '-C', r, 'status', '--short'])\n",
            "subprocess.run(['git', 'diff', *revs])\n",
        ] {
            assert_eq!(scan_python(bad).expect("lexes").len(), 1, "{bad}");
        }
    }

    #[test]
    fn tests_are_not_read() {
        for t in [
            "tests/test_x.py",
            "pkg/tests/a.sh",
            "test_x.py",
            "a/x_test.py",
            "conftest.py",
        ] {
            assert!(is_test(t), "{t}");
        }
        for s in [
            "tools/loopq.py",
            "lib/gitrun.py",
            "contest.py",
            "latest/x.py",
        ] {
            assert!(!is_test(s), "{s}");
        }
    }
}
