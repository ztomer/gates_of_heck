//! A release step that can fail never discards its output (`goh release-quiet`, GOH.RELHIDE).
//!
//! `monitor`'s `./release.sh v0.69.2` stopped at "cargo test failed -- nothing tagged" with the
//! test's output sent to /dev/null, so nobody could see which test failed (M.RELFAIL, fixed in
//! monitor 2a0a3ce with a `step()` helper: quiet on success, the output's tail on failure). The
//! estate sweep found the same shape in five more release scripts. This is that rule as a house
//! check: in a RELEASE SCRIPT (a tracked shell source whose file name holds `release`), a GATE,
//! TEST, INSTALL or VERIFY step whose STDOUT goes to /dev/null is red.
//!
//! WHAT IS A STEP: the command a segment runs (after `if`, `!`, `then`, env assignments, `bash` /
//! `sh` / `env` / `exec` / `time`), when it is
//! * a script or binary whose name says it gates, tests, installs, checks or verifies
//!   (`tools/gate.sh`, `./install.sh`, `scripts/install.sh`, `bin/ab_test`, `local_ci.sh`);
//! * `make` (any target: a release's `make` is its gate);
//! * a toolchain's test/gate/install verb: `cargo test|nextest|clippy|check|install`, `swift
//!   test|build`, `npm|pnpm|yarn test|ci|install`, `go test|vet`, `brew fetch|install|test|audit`,
//!   `pytest`, `python -m pytest`, `xcodebuild`, `just`, `tox`, `bats`.
//!
//! A presence probe (`command -v`, `git rev-parse`, `pgrep`) is not a step: its failure message
//! says everything. Only stdout is judged: `2>/dev/null` alone leaves a test runner's report (on
//! stdout) visible. A segment inside a quoted string is still read, because a `VERIFY='...'`
//! string that runs `./install.sh >/dev/null` is a step the release runs later (`app_updates`,
//! `sys_updater`, `ztools`).
//!
//! The one escape is `# release-quiet-ok: <reason>` on the line. The fix is `monitor`'s `step()`.
//!
//! WIRING: a structural step in EVERY repo. The estate's sites on the day it landed are seeded in
//! `release_quiet_seed.json` (`crate::estate_seed`): seeded files warn, anything else is red.

use crate::estate_seed::{self, Kind, Report, Sites};
use std::path::Path;

const TAG: &str = "[release_quiet]";
const MARKER: &str = "release-quiet-ok:";
/// The step's label, the same line as in `structural_steps.txt`.
pub const LABEL: &str = "no release step hides its output";

/// The seed of the estate's sites on the day the check landed.
pub const KIND: Kind = Kind {
    file: "release_quiet_seed.json",
    unit: "quiet step",
    name: "release-quiet seed",
};

const FIX: &str = "    A release that fails here prints \"... failed -- nothing tagged\" and nothing else: the\n    \
     failing test's name went to /dev/null (monitor's v0.69.2, M.RELFAIL). Run the step through a\n    \
     helper that is quiet on success and shows the output on failure (monitor 2a0a3ce):\n      \
     step() { local label=\"$1\"; shift; local log; log=\"$(mktemp \"${TMPDIR:-/tmp}/release-step.XXXXXX\")\"\n        \
     if \"$@\" >\"$log\" 2>&1; then rm -f \"$log\"; return 0; fi\n        \
     echo \"-- $label failed; its output (full log: $log) --\" >&2; tail -n 150 \"$log\" >&2; return 1; }\n      \
     step \"gate\" ./tools/gate.sh --full || die \"gate failed -- nothing tagged\"\n    \
     A step whose output is genuinely noise says why: `# release-quiet-ok: <reason>`.\n";

/// Is `rel` a release script by name? (Its shell-ness is `crate::shellsrc`'s.)
#[must_use]
pub fn is_release_script(rel: &str) -> bool {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    name.to_ascii_lowercase().contains("release")
}

/// Does `stem` (a file name without its extension) name a gate, test, install or verify step?
fn names_a_step(stem: &str) -> bool {
    const WORDS: [&str; 9] = [
        "gate", "gates", "test", "tests", "install", "ci", "check", "checks", "verify",
    ];
    stem.to_ascii_lowercase()
        .split(['_', '-', '.'])
        .any(|w| WORDS.contains(&w))
}

/// The step a segment's words run, if it is one: a short description for the listing.
fn step_of(words: &[&str]) -> Option<String> {
    const SKIP: [&str; 13] = [
        "if", "!", "then", "else", "elif", "do", "while", "until", "exec", "time", "env", "bash",
        "sh",
    ];
    let mut i = 0;
    while i < words.len() {
        let w = words[i];
        let assign = w.split_once('=').is_some_and(|(k, _)| {
            !k.is_empty() && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
        });
        if SKIP.contains(&w) || w == "zsh" || assign || (w.starts_with('-') && i > 0) {
            i += 1;
            continue;
        }
        break;
    }
    let cmd = *words.get(i)?;
    let sub = words.get(i + 1).copied().unwrap_or("");
    let base = cmd.rsplit('/').next().unwrap_or(cmd);
    let verbs: &[&str] = match base {
        "make" | "gmake" | "pytest" | "xcodebuild" | "just" | "tox" | "bats" => {
            return Some(format!("`{base}`"));
        }
        "command" | "type" | "which" | "hash" => return None,
        "cargo" => &["test", "nextest", "clippy", "check", "install"],
        "swift" => &["test", "build"],
        "npm" | "pnpm" | "yarn" => &["test", "ci", "install"],
        "go" => &["test", "vet"],
        "brew" => &["fetch", "install", "test", "audit"],
        b if b.starts_with("python") => {
            return (sub == "-m" && words.get(i + 2) == Some(&"pytest"))
                .then(|| format!("`{base} -m pytest`"));
        }
        _ => {
            let stem = base.split('.').next().unwrap_or(base);
            return names_a_step(stem).then(|| format!("`{cmd}`"));
        }
    };
    verbs.contains(&sub).then(|| format!("`{base} {sub}`"))
}

/// Does this segment send its stdout to /dev/null (`>`, `1>`, `>>`, `&>`, `>&`)?
fn discards_stdout(seg: &str) -> bool {
    let b = seg.as_bytes();
    let mut from = 0;
    while let Some(k) = seg[from..].find("/dev/null") {
        let at = from + k;
        from = at + 1;
        let mut j = at;
        while j > 0 && b[j - 1] == b' ' {
            j -= 1;
        }
        if j >= 2 && b[j - 1] == b'&' && b[j - 2] == b'>' {
            j -= 1; // `>&/dev/null`: judged as the `>` before it
        }
        if j == 0 || b[j - 1] != b'>' {
            continue;
        }
        let mut op = j - 1;
        if op > 0 && b[op - 1] == b'>' {
            op -= 1;
        }
        let before = if op > 0 { b[op - 1] } else { b' ' };
        if before == b'&' || before == b'1' || before.is_ascii_whitespace() || op == 0 {
            if before == b'1' && op >= 2 && b[op - 2].is_ascii_digit() {
                continue;
            }
            return true;
        }
    }
    false
}

/// The quiet steps in one release script: `(1-based line, the line, the step)`.
#[must_use]
pub fn scan_text(text: &str) -> Vec<(usize, String, String)> {
    let mut out = Vec::new();
    let lines: Vec<&str> = text.lines().collect();
    let mut n = 0;
    while n < lines.len() {
        let first = n;
        let mut logical = String::new();
        loop {
            let l = lines[n];
            n += 1;
            if let Some(cont) = l.strip_suffix('\\') {
                logical.push_str(cont);
                logical.push(' ');
                if n < lines.len() {
                    continue;
                }
            } else {
                logical.push_str(l);
            }
            break;
        }
        let t = logical.trim_start();
        if t.starts_with('#') || logical.contains(MARKER) || !logical.contains("/dev/null") {
            continue;
        }
        for seg in segments(&logical) {
            if !discards_stdout(seg) {
                continue;
            }
            let words: Vec<&str> = seg
                .split_whitespace()
                .map(word)
                .filter(|w| !w.is_empty())
                .collect();
            if let Some(step) = step_of(&words) {
                out.push((first + 1, lines[first].to_string(), step));
                break;
            }
        }
    }
    out
}

/// A logical line cut into the commands it runs: at `;`, `&&`, `||`, `|`, a background `&`, a
/// single quote, backticks, parentheses and braces -- never at the `&` of a redirect (`&>`, `>&`,
/// `2>&1`). Crude by design: a cut too many cannot split a step from its redirect, because a
/// redirect is written after its command in the same segment.
fn segments(line: &str) -> Vec<&str> {
    let b = line.as_bytes();
    let mut out = Vec::new();
    let mut start = 0;
    for (i, &c) in b.iter().enumerate() {
        let cut = match c {
            b';' | b'|' | b'\'' | b'`' | b'(' | b')' | b'{' | b'}' => true,
            b'&' => {
                let prev = if i > 0 { b[i - 1] } else { b' ' };
                let next = b.get(i + 1).copied().unwrap_or(b' ');
                prev != b'>' && next != b'>'
            }
            _ => false,
        };
        if cut {
            out.push(&line[start..i]);
            start = i + 1;
        }
    }
    out.push(&line[start..]);
    out.into_iter().filter(|s| !s.trim().is_empty()).collect()
}

/// A word as the step reader sees it: quotes off its ends, and an assignment whose value opens a
/// double-quoted string (`VERIFY="./install.sh`) read as that string's first word.
fn word(w: &str) -> &str {
    if let Some((k, v)) = w.split_once("=\"") {
        if !k.is_empty()
            && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && !v.is_empty()
            && !v.contains('"')
        {
            return v.trim_matches('"');
        }
    }
    w.trim_matches('"')
}

/// The sites in the release scripts among `files`.
fn sites(root: &Path, files: &[String], exclude: &str, staged: bool) -> Result<Sites, String> {
    let exclude = crate::steps::compile_exclude(exclude)?;
    let named: Vec<String> = files
        .iter()
        .filter(|f| is_release_script(f))
        .cloned()
        .collect();
    let sources = crate::shellsrc::sources(root, &named, exclude.as_ref(), staged);
    crate::subject::record(&crate::subject::RELEASE_QUIET, sources.len());
    let mut out = Sites::new();
    for (rel, text) in &sources {
        let found: Vec<String> = scan_text(text)
            .into_iter()
            .map(|(n, src, step)| {
                format!(
                    "    {rel}:{n}: {}  -- {step} sends its output to /dev/null",
                    estate_seed::clip(&src)
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
    let seed = match estate_seed::load(root, KIND, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let found = match sites(root, files, exclude, staged) {
        Ok(s) => s,
        Err(m) => return (2, String::new(), format!("{ERR} {TAG} {m}\n")),
    };
    let scripts = files.iter().filter(|f| is_release_script(f)).count();
    let report = Report {
        tag: TAG,
        item: "GOH.RELHIDE",
        what: (
            "release step that sends its output to /dev/null",
            "release steps that send their output to /dev/null",
        ),
        fix: FIX,
        ok: format!("{scripts} release-named file(s), no gate/test/install step hides its output"),
    };
    estate_seed::verdict(root, staged, KIND, &seed, &found, &report)
}

/// `goh release-quiet [--staged] [--exclude RE]`.
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
        "crates/goh/src/relquiet.rs",
        judge(repo, files, &cfg.exclude, staged),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn steps(src: &str) -> Vec<usize> {
        scan_text(src).into_iter().map(|(n, _, _)| n).collect()
    }

    #[test]
    fn the_estates_quiet_steps_are_found() {
        let src = "\
./tools/gate.sh --full >/dev/null || die \"gate failed\"
make ci >/dev/null
VERIFY='./install.sh >/dev/null
bash tools/gate.sh --full >/dev/null || die x
brew fetch --cask \"ztomer/tap/antiknob\" --force >/dev/null 2>&1 || die y
ZT_DIR=\"$(mktemp -d)\" && ZTOOLS_INSTALL_DIR=\"$ZT_DIR\" ./install.sh --dev-copy >/dev/null
cargo test --workspace &>/dev/null
if ! pytest -q > /dev/null; then exit 1; fi
VERIFY=\"scripts/install.sh >/dev/null\"
";
        assert_eq!(steps(src), vec![1, 2, 3, 4, 5, 6, 7, 8, 9]);
    }

    #[test]
    fn probes_stderr_only_markers_and_comments_pass() {
        let src = "\
command -v gh >/dev/null || die \"gh CLI required\"
git rev-parse \"${TAG}\" >/dev/null 2>&1 && die \"tag exists\"
cargo test --workspace 2>/dev/null || die z
pgrep -f \"$APP\" >/dev/null || die w
# ./tools/gate.sh --full >/dev/null
make ci >/dev/null  # release-quiet-ok: ci prints its own summary to stderr
tools/release_note_tests.sh </dev/null \\
  | tee notes.txt
step \"gate\" ./tools/gate.sh --full || die \"gate failed\"
brew update >/dev/null 2>&1
./bump.sh \"$NEW\" >/dev/null
";
        assert_eq!(steps(src), Vec::<usize>::new());
    }

    #[test]
    fn a_continued_line_is_one_step_named_at_its_first_line() {
        let src = "true\n./install.sh --dev \\\n  --quiet >/dev/null || die x\n";
        assert_eq!(steps(src), vec![2]);
    }

    #[test]
    fn only_release_named_files_are_in_scope() {
        assert!(is_release_script("tools/release.sh"));
        assert!(is_release_script("scripts/push_release.sh"));
        assert!(is_release_script("release/build-release.sh"));
        assert!(!is_release_script("tools/gate.sh"));
    }
}
