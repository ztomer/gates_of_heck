//! `goh red-run`: a commit that adds a test names the mutation that turned it red (BACKLOG 4.11).
//!
//! "Prove a new test can fail" is written in this repo's rules, `ZoneWM`'s and the global ones, and
//! was enforced nowhere: the pass cache records passes only, so a test that cannot fail reads exactly
//! like one that can. The commit message is where the question cannot be skipped. A commit whose diff
//! ADDS a test declaration carries `Red-run:` in git's trailer block -- what was broken, and that the
//! new test went red on it -- beside `commit-class`'s trailers, read by the same parser.
//!
//! What it does not do: check that the claim is TRUE. A trailer is checked for presence and for
//! substance (`none`, `n/a`, `tbd`, `-`, `yes`, `done` are refused, as gaming `Systemic:` looked);
//! whether the named mutation really turned the test red stays a review rule, and says so here.
//!
//! Opt-in per repo by `GOH_RED_RUN` in `.gatesrc`: the stock commit-msg hook asks at commit time
//! (`gates/commit_msg.sh`), and `push_gate.sh` asks again over the pushed range.

use crate::commit_class::{cleaned, final_trailers};
use goh_tui::ERR;

const KEY: &str = "Red-run";
const TAG: &str = "[red_run]";
/// What an empty claim looked like elsewhere; a value starting with one of these says nothing.
const HOLLOW: [&str; 6] = ["none", "n/a", "tbd", "-", "yes", "done"];

/// Whether `line`, in the file at `path`, declares a test. By language, at the line's start, so a
/// string or a comment naming `def test_` is not one.
#[must_use]
pub fn declares_test(path: &str, line: &str) -> bool {
    let t = line.trim_start();
    let ext = path.rsplit_once('.').map_or("", |(_, e)| e);
    match ext {
        "py" => t.starts_with("def test") || t.starts_with("async def test"),
        "rs" => t.starts_with("#[test]") || t.starts_with("#[tokio::test"),
        "swift" => t.starts_with("func test") || t.starts_with("@Test"),
        "js" | "ts" | "mjs" | "cjs" | "jsx" | "tsx" => {
            t.starts_with("it(") || t.starts_with("test(")
        }
        "bats" => t.starts_with("@test "),
        _ => false,
    }
}

/// Every test declaration a unified diff ADDS, as `path: line`, in diff order.
#[must_use]
pub fn added_tests(diff: &str) -> Vec<String> {
    let mut path = String::new();
    let mut out = Vec::new();
    for line in diff.lines() {
        if let Some(p) = line.strip_prefix("+++ ") {
            p.strip_prefix("b/").unwrap_or(p).clone_into(&mut path);
        } else if let Some(added) = line.strip_prefix('+') {
            if declares_test(&path, added) {
                out.push(format!("{path}: {}", added.trim()));
            }
        }
    }
    out
}

/// Why `message` may not commit `diff`, or None.
#[must_use]
pub fn refusal(message: &str, diff: &str) -> Option<String> {
    let added = added_tests(diff);
    let first = added.first()?;
    let trailers = final_trailers(&cleaned(message));
    let claim = trailers
        .get(KEY)
        .map(|v| v.to_lowercase())
        .unwrap_or_default();
    if !claim.is_empty() && !HOLLOW.iter().any(|h| claim.starts_with(h)) {
        return None;
    }
    let more = match added.len() {
        1 => String::new(),
        n => format!(" and {} more", n - 1),
    };
    Some(format!(
        "it adds a test ({first}{more}) and its trailer block carries no `{KEY}:` naming the \
         mutation that turned it red -- break the code once, watch the test fail, and say what broke"
    ))
}

/// git's stdout as text, through `commit_class`'s one spawn (the census of git spawns stays one).
fn git(args: &[&str]) -> Result<String, String> {
    crate::commit_class::git(args).map(|out| String::from_utf8_lossy(&out).into_owned())
}

#[must_use]
pub fn run(file: Option<&str>, range: &[String]) -> i32 {
    match file {
        Some(f) => check_file(f),
        None if !range.is_empty() => check_range(range),
        None => {
            goh_tui::eoutln!("{ERR} {TAG} goh red-run: FILE or --range REV...");
            2
        }
    }
}

/// `goh red-run FILE`: the message git is about to commit, against the staged diff.
#[must_use]
pub fn check_file(path: &str) -> i32 {
    let Ok(message) = std::fs::read_to_string(path) else {
        goh_tui::eoutln!("{ERR} {TAG} cannot read the message file {path}");
        return 2;
    };
    let diff = match git(&["diff", "--cached", "-U0", "--no-color", "--no-ext-diff"]) {
        Ok(d) => d,
        Err(e) => {
            goh_tui::eoutln!("{ERR} {TAG} {e}");
            return 2;
        }
    };
    refusal(&message, &diff).map_or(0, |why| {
        goh_tui::eoutln!("{ERR} {TAG} commit refused: {why}");
        1
    })
}

/// `goh red-run --range REV...`: every commit the range names, each against its own diff.
#[must_use]
pub fn check_range(revs: &[String]) -> i32 {
    let mut args = vec!["rev-list", "--reverse", "--no-merges"];
    args.extend(revs.iter().map(String::as_str));
    let shas = match git(&args) {
        Ok(s) => s,
        Err(e) => {
            goh_tui::eoutln!("{ERR} {TAG} {e}");
            return 2;
        }
    };
    let mut refused = 0;
    for sha in shas.lines() {
        let read = git(&["log", "-1", "--format=%B", sha]).and_then(|m| {
            git(&[
                "show",
                "--format=",
                "-U0",
                "--no-color",
                "--no-ext-diff",
                sha,
            ])
            .map(|d| (m, d))
        });
        let (message, diff) = match read {
            Ok(pair) => pair,
            Err(e) => {
                goh_tui::eoutln!("{ERR} {TAG} {e}");
                return 2;
            }
        };
        if let Some(why) = refusal(&message, &diff) {
            let short = sha.get(..8).unwrap_or(sha);
            goh_tui::eoutln!("{ERR} {TAG} {short} refused: {why}");
            refused += 1;
        }
    }
    i32::from(refused > 0)
}

#[cfg(test)]
#[path = "red_run_tests.rs"]
mod tests;
