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
/// What an empty claim looked like elsewhere. A claim that IS one of these -- trimmed, case-folded,
/// trailing punctuation dropped -- says nothing; one that merely begins with one ("none is a name",
/// "`done_state` ...", "-U1 diff") is a claim (GOH.REDRUNHOLLOW).
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

/// Whether `line` declares the item an attribute line above it marks (`fn`, `func`).
fn declares_item(line: &str) -> bool {
    let t = line.trim_start();
    let t = t.strip_prefix("pub ").unwrap_or(t);
    let t = t.strip_prefix("async ").unwrap_or(t);
    t.starts_with("fn ") || t.starts_with("func ")
}

/// The name a test declaration line gives its test: the identifier after `def`/`fn`/`func`, or a
/// JS/bats test's quoted title. None for a bare attribute line (`#[test]`, `@Test`), whose name is
/// on the declaration below it.
fn test_name(line: &str) -> Option<String> {
    let t = line.trim_start();
    for kw in ["def ", "fn ", "func "] {
        if let Some(at) = t.find(kw).filter(|&i| i == 0 || t[..i].ends_with(' ')) {
            let rest = &t[at + kw.len()..];
            let name: String = rest
                .chars()
                .take_while(|c| c.is_alphanumeric() || *c == '_')
                .collect();
            return (!name.is_empty()).then_some(name);
        }
    }
    let open = t.find(['"', '\'', '`'])?;
    let quote = t[open..].chars().next()?;
    let body = &t[open + 1..];
    body.find(quote).map(|close| body[..close].to_owned())
}

/// One test declaration a diff side carries: its `path: line`, and its name when it has one.
struct Declared {
    path: String,
    shown: String,
    name: Option<String>,
}

/// The test declarations on one side of a unified diff (`+` added, `-` removed), in diff order. An
/// attribute line (`#[test]`) names the test declared below it; a declaration line under an
/// attribute that is context (a test renamed in place, `app_updates` de40622) is one too -- the
/// diff is read at `-U1` so that context line is there to see.
fn declared(diff: &str, side: char) -> Vec<Declared> {
    let mut path = String::new();
    let mut out: Vec<Declared> = Vec::new();
    let mut context_attr = false;
    // An attribute on this side whose name is on the next declaration line of this side.
    let mut pending: Option<usize> = None;
    for line in diff.lines() {
        if let Some(p) = line.strip_prefix("+++ ") {
            p.strip_prefix("b/").unwrap_or(p).clone_into(&mut path);
            context_attr = false;
            pending = None;
        } else if line.starts_with("--- ") {
            context_attr = false;
            pending = None;
        } else if let Some(this) = line.strip_prefix(side) {
            if let Some(i) = pending.filter(|_| declares_item(this)) {
                out[i].name = test_name(this);
                pending = None;
            } else if declares_test(&path, this) || (context_attr && declares_item(this)) {
                let name = test_name(this);
                pending = name.is_none().then_some(out.len());
                out.push(Declared {
                    path: path.clone(),
                    shown: format!("{path}: {}", this.trim()),
                    name,
                });
            }
            context_attr = false;
        } else if let Some(kept) = line.strip_prefix(' ') {
            context_attr = declares_test(&path, kept);
            pending = None;
        } else if !line.starts_with(['+', '-']) {
            context_attr = false;
            pending = None;
        }
    }
    out
}

/// Every test declaration a unified diff ADDS, as `path: line`, in diff order.
///
/// Keyed by NAME per file (GOH.GATEHONEST): a test the same diff removes from the same file under the same name is
/// the same test with a new signature or place (`func testA() {` -> `func testA() throws {`), not a
/// new one. A rename is a new name, so it is still an added test.
#[must_use]
pub fn added_tests(diff: &str) -> Vec<String> {
    let removed: std::collections::HashSet<(String, String)> = declared(diff, '-')
        .into_iter()
        .filter_map(|d| d.name.map(|n| (d.path, n)))
        .collect();
    declared(diff, '+')
        .into_iter()
        .filter(|d| {
            d.name
                .as_ref()
                .is_none_or(|n| !removed.contains(&(d.path.clone(), n.clone())))
        })
        .map(|d| d.shown)
        .collect()
}

/// Why `message` may not commit `diff`, or None.
#[must_use]
pub fn refusal(message: &str, diff: &str) -> Option<String> {
    let added = added_tests(diff);
    let first = added.first()?;
    let lines = cleaned(message);
    if let Some(line) = crate::commit_class::unread_block(&lines, &[KEY]) {
        return Some(crate::commit_class::unread_block_refusal(&line));
    }
    let trailers = final_trailers(&lines);
    let claim = trailers
        .get(KEY)
        .map(|v| hollow_form(v))
        .unwrap_or_default();
    if !claim.is_empty() && !HOLLOW.contains(&claim.as_str()) {
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

/// A claim as it is judged hollow: trimmed, case-folded, trailing `.,;:!?` dropped.
fn hollow_form(claim: &str) -> String {
    claim
        .trim()
        .trim_end_matches(['.', ',', ';', ':', '!', '?'])
        .trim_end()
        .to_lowercase()
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
    let diff = match git(&["diff", "--cached", "-U1", "--no-color", "--no-ext-diff"]) {
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

/// Does the commit's parent have `.gatesrc` with `GOH_RED_RUN` enabled?
///
/// A commit placed above older unpushed commits must not refuse those older commits, which were
/// made before the rule was enabled (BACKLOG / GOH.RR1). No `.gatesrc` at the parent means off.
fn parent_opts_in(sha: &str) -> bool {
    let spec = format!("{sha}^:.gatesrc");
    let Ok(content) = git(&["show", &spec]) else {
        return false;
    };
    let pairs = crate::gatesrc::parse_pairs(&content);
    pairs
        .get("GOH_RED_RUN")
        .is_some_and(|v| !v.is_empty() && v != "0")
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
        if !parent_opts_in(sha) {
            continue;
        }
        let read = git(&["log", "-1", "--format=%B", sha]).and_then(|m| {
            git(&[
                "show",
                "--format=",
                "-U1",
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
