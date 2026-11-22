//! bash never sets a locale variable itself; a command gets one through `env` (`goh locale-prefix`,
//! EST.LOCALEPREFIX).
//!
//! THE CRASH (GOH.PROVENBUILD, v0.122.0's release, 2026-11-15). A temporary `LC_ALL=C` on a command
//! that is not the last of a forked bash subshell is disposed of IN that subshell, and bash re-reads
//! its locale (`dispose_temporary_env` -> `sv_locale` -> `libintl_setlocale`). Homebrew's bash links
//! gettext's libintl, which with no LANG set asks CoreFoundation for the user's preferred languages
//! in a process that forked without exec: SIGSEGV, "crashed on child side of fork pre-exec", and
//! bash's EXIT path runs with `$?` still 0, so the subshell's work silently stops. Load- and
//! timing-dependent, so a test that passes alone fails one run in twenty under `-n 12`.
//!
//! WHAT IS JUDGED: every line of a bash source (`crate::shellsrc`, less zsh: its locale handling is
//! not bash's) that assigns `LC_*`, `LANG` or `LANGUAGE` -- a temporary prefix (`LC_ALL=C sort`), an
//! `export`, `local` or plain assignment -- or `unset`s one, unless the assignment is an argument of
//! `env` (`env LC_ALL=C sort` sets it in the child `env` execs; bash's own locale never moves).
//! Any of them can run in a forked subshell: a pipeline element, `( )`, `$( )`, `<( )`, a function
//! called from one. Comments are not read. goh's own tree was pinned by `tests/test_shell_locale.py`
//! since GOH.PROVENBUILD; this is that rule for every consumer.
//!
//! WIRING: a structural step in EVERY repo, judged from its own grace's end, `OVER_FROM`
//! (2026-11-30); before it the scan reads (the floor counts it) and warns. The sites a consumer
//! keeps are seeded in `locale_prefix_seed.json` (`crate::estate_seed`): seeded files warn,
//! anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use regex::Regex;
use std::path::Path;
use std::sync::LazyLock;

const TAG: &str = "[locale_prefix]";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no locale variable set by bash itself";
/// 2026-11-30T00:00:00Z, seconds since the epoch: this scanner's own grace end
/// (`crate::grace::over_from`).
pub const OVER_FROM: u64 = 1_795_996_800;

/// The seed of the sites a consumer keeps.
pub const KIND: Kind = Kind {
    file: "locale_prefix_seed.json",
    unit: "locale site",
    name: "locale-prefix seed",
};

const FIX: &str = "    A locale variable bash sets itself (a temporary prefix, export, local, assignment or unset)\n    \
     is re-read when it is disposed of, and in a forked subshell Homebrew bash's libintl asks\n    \
     CoreFoundation for the preferred languages after fork: SIGSEGV, and the subshell exits 0 having\n    \
     done nothing (GOH.PROVENBUILD). Give the command its locale through env, which bash never reads:\n      \
     env LC_ALL=C sort       (not LC_ALL=C sort)\n";

static LOCALE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?:LC_[A-Z]+|LANG|LANGUAGE)=").expect("static regex"));
static UNSET: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?:^|[\s;&|({])unset\s+(?:-v\s+)?[^;&|]*\b(?:LC_[A-Z]+|LANG|LANGUAGE)\b")
        .expect("static regex")
});
/// The command a match belongs to starts after the last of these on its line.
static SEPARATOR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\$\(|<\(|>\(|&&|\|\||[|;&(){}`]").expect("static regex"));
/// `env` (after any keyword that leads a command: `if env ...`, `exec env ...`), its options (an
/// option may take one bare word: `-u NAME`), and assignments before the match: the match is one of
/// env's arguments.
static ENV: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:(?:if|elif|then|else|do|while|until|exec|command|time|!)\s+)*env(?:\s+-\S+(?:\s+[A-Za-z_]\w*)?)*\s+(?:[A-Za-z_]\w*=\S*\s+)*$")
        .expect("static regex")
});
static COMMENT: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?:^|\s)#").expect("static regex"));

/// The line without a trailing comment (a `#` at the start or after whitespace).
fn code(line: &str) -> &str {
    COMMENT.find(line).map_or(line, |m| &line[..m.start()])
}

/// Whether `line` has bash itself set or unset a locale variable.
#[must_use]
pub fn sets_locale(raw: &str) -> bool {
    let line = code(raw);
    if UNSET.is_match(line) {
        return true;
    }
    LOCALE.find_iter(line).any(|m| {
        let before = line[..m.start()].chars().next_back();
        if before.is_some_and(|c| c.is_alphanumeric() || "_$-".contains(c)) {
            return false; // MY_LANG=, ${LC_ALL}= ... are other names
        }
        let head = SEPARATOR.split(&line[..m.start()]).last().unwrap_or("");
        !ENV.is_match(head)
    })
}

/// The 1-based lines of `text` that set a locale in bash.
#[must_use]
pub fn scan(text: &str) -> Vec<usize> {
    text.lines()
        .enumerate()
        .filter(|(_, l)| sets_locale(l))
        .map(|(i, _)| i + 1)
        .collect()
}

/// Whether a shell source is zsh's, whose locale handling is not bash's.
fn is_zsh(rel: &str, text: &str) -> bool {
    rel.ends_with(".zsh")
        || text
            .lines()
            .next()
            .is_some_and(|l| l.starts_with("#!") && l.contains("zsh"))
}

/// The lines `goh locale-prefix` judges in one file: its findings, through its own selection.
#[must_use]
pub fn judged_lines(rel: &str, text: &str) -> Vec<usize> {
    if is_zsh(rel, text) {
        Vec::new()
    } else {
        scan(text)
    }
}

/// The sites in the bash sources among `files`.
fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let sources: Vec<(String, String)> =
        crate::shellsrc::sources(root, files, exclude.as_ref(), staged)
            .into_iter()
            .filter(|(rel, text)| !is_zsh(rel, text))
            .collect();
    crate::subject::record(&crate::subject::LOCALE_PREFIX, sources.len());
    let mut out = Sites::new();
    for (rel, text) in &sources {
        let rows: Vec<&str> = text.lines().collect();
        let found: Vec<String> = scan(text)
            .into_iter()
            .map(|n| {
                let line = rows.get(n - 1).copied().unwrap_or("");
                format!(
                    "    {rel}:{n}: {}  -- bash sets a locale itself: give it through env",
                    estate_seed::clip(line)
                )
            })
            .collect();
        if !found.is_empty() {
            out.insert(rel.clone(), found);
        }
    }
    Ok(out)
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], exclude: &str, staged: bool) -> (i32, String, String) {
    use goh_tui::ERR;
    if !crate::grace::over_from(OVER_FROM) {
        // Read (the floor counts it) and never judged within the grace; what the day finds is the
        // warning (GOH.GRACESHAPES), and no seed entry written now reads as stale (GOH.GRACEEND).
        let day = sites(root, files, exclude, staged);
        return crate::grace::ahead::warn_only((root, staged), (KIND, TAG), day);
    }
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
        item: "EST.LOCALEPREFIX",
        what: (
            "locale variable bash sets itself",
            "locale variables bash sets itself",
        ),
        fix: FIX,
        ok: "every locale reaches its command through env".to_string(),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh locale-prefix [--staged] [--exclude RE]`.
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
        "crates/goh/src/localeprefix.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

/// The shapes `goh locale-prefix` judges (`crate::shapes`).
pub const SHAPES: crate::shapes::Declared = {
    use crate::shapes::{hooks, Declared, Shape, Token, Verdict};
    Declared {
        probe: r"(?:^|[^\w$-])(?:LC_[A-Z]+|LANG|LANGUAGE)=|unset\s.*\b(?:LC_[A-Z]+|LANG|LANGUAGE)\b",
        judged: hooks::locale_prefix,
        owns: &[Token {
            name: "a locale assignment",
            sample: ("a.sh", "LC_ALL=C sort x\n"),
        }],
        sources: &[
            "crates/goh/src/localeprefix.rs",
            "crates/goh/src/shellsrc.rs",
        ],
        opt_in: None,
        shapes: &[
            Shape {
                name: "a temporary prefix on a command",
                path: r"\.sh$",
                line: r"=\S+\s+\S",
                verdict: Verdict::Covered,
                fixture: ("a.sh", "find . | LC_ALL=C sort\n"),
            },
            Shape {
                name: "an export, local or plain assignment",
                path: r"\.sh$",
                line: r"^\s*(?:export|local)?\s*\w+=",
                verdict: Verdict::Covered,
                fixture: ("a.sh", "export LANG=C\n"),
            },
            Shape {
                name: "an unset",
                path: r"\.sh$",
                line: r"unset",
                verdict: Verdict::Covered,
                fixture: ("a.sh", "unset LC_ALL\n"),
            },
            Shape {
                name: "an argument of env",
                path: r"\.sh$",
                line: r"\benv\s",
                verdict: Verdict::OutOfScope(
                    "env sets it in the child it execs; bash's own locale never moves",
                ),
                fixture: ("a.sh", "find . | env LC_ALL=C sort\n"),
            },
            Shape {
                name: "a zsh source",
                path: r"\.zsh$",
                line: "",
                verdict: Verdict::OutOfScope(
                    "the crash is bash's (libintl after fork); zsh's locale is its own",
                ),
                fixture: ("a.zsh", "LC_ALL=C sort x\n"),
            },
        ],
    }
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shape_that_crashed_is_found() {
        let crashed =
            "{\n    LC_ALL=C comm -13 \"$1\" <(printf '%s\\n' \"$now\")\n} | LC_ALL=C sort -u\n";
        assert_eq!(scan(crashed), vec![2, 3]);
        let fixed = scan(&crashed.replace("LC_ALL=C", "env LC_ALL=C"));
        assert!(fixed.is_empty(), "expected no rows: {fixed:?}");
        for bad in [
            "export LANG=C",
            "local LC_ALL=C",
            "LC_COLLATE=C",
            "unset LC_ALL",
            "x=$(LC_ALL=C sort)",
            "a | LANGUAGE=en tr a b",
            "if LC_ALL=C grep -q x f; then",
        ] {
            assert!(sets_locale(bad), "{bad}");
        }
    }

    #[test]
    fn env_comments_and_other_names_pass() {
        for ok in [
            "env -u LANG LC_ALL=C sort",
            "env LC_ALL=C GOH_X=1 sort",
            "# LC_ALL=C sort",
            "echo \"$LC_ALL\"",
            "MY_LANG=x run",
            "x=1  # LC_ALL=C is set by env",
            "if env LC_ALL=C grep -q x f; then",
            "[ \"${LC_ALL:-}\" = C ] || exec env LC_ALL=C bash \"$0\" \"$@\"",
            "while ! env LANG=C test -f x; do",
        ] {
            assert!(!sets_locale(ok), "{ok}");
        }
    }

    #[test]
    fn zsh_is_not_judged() {
        for (rel, text) in [
            ("a.zsh", "LC_ALL=C sort\n"),
            ("bin/x", "#!/bin/zsh\nLC_ALL=C sort\n"),
        ] {
            let got = judged_lines(rel, text);
            assert!(got.is_empty(), "expected no rows: {got:?}");
        }
        assert_eq!(
            judged_lines("a.sh", "#!/usr/bin/env bash\nLC_ALL=C sort\n"),
            vec![2]
        );
    }
}
