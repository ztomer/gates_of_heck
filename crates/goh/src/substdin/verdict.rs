//! The verdict over one scan: the exit code and the report text, each finding, refusal and stale
//! exemption named with what to do about it.

use super::{allow, Outcome, Policy, MAX_SHOWN, TAG};
use goh_tui::{ERR, OK, START};
use std::fmt::Write as _;
use std::path::Path;

/// The verdict: (exit code, report).
#[must_use]
pub fn report(
    outcome: &Outcome,
    root: &Path,
    listed: &[String],
    staged: bool,
    policy: &Policy,
) -> (i32, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let (entries, problems) = allow::load(root);
    let mut s = String::new();
    if !problems.is_empty() {
        for p in &problems {
            let _ = writeln!(s, "{ERR} {TAG} {p}");
        }
        let _ = writeln!(
            s,
            "{START} An allowlist that will not parse is NOT an empty one: fix {file}, or the gate \
             cannot see what it is exempting.",
            file = allow::ALLOW_FILE
        );
        return (1, s);
    }
    if let Some(code) = scope_verdict(outcome, staged, policy, &mut s) {
        return (code, s);
    }
    let (left, allowed, stale) = allow::partition(&outcome.findings, &entries, listed, root);
    findings_verdict(&mut s, &left, outcome, scope);
    unreadable_verdict(&mut s, outcome);
    stale_verdict(&mut s, &entries, &stale);
    if !left.is_empty() || !stale.is_empty() || !outcome.unreadable.is_empty() {
        return (1, s);
    }
    let debt = entries.iter().filter(|e| e.unreviewed).count();
    let _ = write!(
        s,
        "{OK} {TAG} OK — {examined} {scope} Python file(s), every one of {seen} process call(s) \
         declares its stdin",
        examined = outcome.examined,
        seen = outcome.seen,
    );
    if allowed > 0 {
        let _ = write!(
            s,
            " ({allowed} exempt in {file}, {debt} 'unreviewed' -- seeded when the gate landed, \
             delete each as you fix it)",
            file = allow::ALLOW_FILE
        );
    }
    s.push('\n');
    (0, s)
}

/// The verdict BEFORE any finding is judged: the scope's own three answers, then the floor.
/// Returns `Some(code)` when it has already spoken -- including the PASSING one, so "not
/// applicable" can never fall through to the findings block and print a second,
/// contradictory verdict under it.
fn scope_verdict(outcome: &Outcome, staged: bool, policy: &Policy, s: &mut String) -> Option<i32> {
    if outcome.tracked_python == 0 {
        let _ = writeln!(
            s,
            "{OK} {TAG} not applicable — this repo tracks no Python source"
        );
        let _ = writeln!(
            s,
            "{START}   looked for every tracked *.py, and there are none, so there is no child \
             process here to have inherited a stdin"
        );
        return Some(0);
    }
    if staged && outcome.examined == 0 {
        // A commit that stages only excluded Python has nothing here to judge; the full scope
        // is where an exclusion that hides the whole subject is red.
        let _ = writeln!(
            s,
            "{OK} {TAG} not applicable — every staged *.py is excluded (GOH_EXCLUDE '{}')",
            policy.exclude
        );
        return Some(0);
    }
    if outcome.examined == 0 {
        // NOT "not applicable": the repo has Python and this check read none of it, which
        // is a blind scanner wearing the clothes of a clean one.
        let _ = writeln!(
            s,
            "{ERR} {TAG} this repo tracks {n} Python file(s) and the gate read NONE of them",
            n = outcome.tracked_python
        );
        let _ = writeln!(
            s,
            "{START}   GOH_EXCLUDE is '{}'. An exclusion that removes the whole subject makes the \
             gate pass over nothing; narrow it, or say why in .gatesrc.",
            policy.exclude
        );
        return Some(1);
    }
    if let Some(want) = policy.min_calls {
        if !staged && outcome.seen < want {
            let _ = writeln!(
                s,
                "{ERR} {TAG} scanned {seen} process call(s) and .gatesrc declares a floor of {want}",
                seen = outcome.seen
            );
            let _ = writeln!(
                s,
                "{START}   A scan that has stopped seeing its subject reads as clean. Find what moved \
                 first -- the floor is GOH_SUBPROCESS_STDIN_MIN_CALLS -- or lower it with the \
                 tree in the same commit, so the change is reviewed."
            );
            return Some(1);
        }
    }
    None
}

/// The findings block, when there is anything to report.
fn findings_verdict(s: &mut String, left: &[allow::Unallowed], outcome: &Outcome, scope: &str) {
    if left.is_empty() {
        return;
    }
    let _ = writeln!(
        s,
        "{ERR} {TAG} {} call(s) leave stdin undeclared in {scope} source:",
        left.len()
    );
    for (path, n) in left.iter().take(MAX_SHOWN) {
        let f = &outcome.findings[*n].1;
        let shown: String = f.text.chars().take(140).collect();
        let _ = writeln!(s, "    {path}:{}: {}", f.line, f.message());
        if !shown.is_empty() {
            let _ = writeln!(s, "      {shown}");
        }
    }
    if left.len() > MAX_SHOWN {
        let _ = writeln!(s, "    … and {} more", left.len() - MAX_SHOWN);
    }
    for line in [
        "A child that reads stdin HANGS when its caller's stdin is a terminal, or a pipe",
        "whose writer never closes. Name stdin=subprocess.DEVNULL, input=..., or stdin=None",
        "to inherit on purpose -- the point is that inheriting was WRITTEN DOWN.",
        "os.system / os.popen / subprocess.getoutput / getstatusoutput cannot declare a",
        "stdin at all; use subprocess.run(..., stdin=...).",
        "A **kwargs call must name it too (stdin=kwargs.pop(\"stdin\", DEVNULL)): a static",
        "read cannot see what the mapping holds.",
    ] {
        let _ = writeln!(s, "{START} {line}");
    }
    let _ = writeln!(
        s,
        "  A call that must stand goes in {file} with a reason. One entry, ONE call site.",
        file = allow::ALLOW_FILE
    );
}

/// The unreadable-files block: a tracked `.py` this check could not parse is itself the
/// defect, and a scan that skipped it would have a scope nobody could account for.
fn unreadable_verdict(s: &mut String, outcome: &Outcome) -> bool {
    if outcome.unreadable.is_empty() {
        return false;
    }
    let _ = writeln!(
        s,
        "{ERR} {TAG} {} tracked Python file(s) could not be read, and a file this check cannot \
         parse is a finding rather than a skip:",
        outcome.unreadable.len()
    );
    for (path, reason) in &outcome.unreadable {
        let _ = writeln!(s, "    {path}: {reason}");
    }
    let _ = writeln!(
        s,
        "{START}   Fix the syntax. Every other finding in this file is unreported while it will not parse."
    );
    true
}

/// The stale-entries block: an exemption outliving its finding is permission nobody
/// re-earns, which is the whole failure mode an allowlist exists to prevent.
fn stale_verdict(s: &mut String, entries: &[allow::Entry], stale: &[usize]) {
    if stale.is_empty() {
        return;
    }
    let _ = writeln!(
        s,
        "{ERR} {TAG} {} stale entr(ies): the finding is gone, the exemption stayed:",
        stale.len()
    );
    s.push_str(&allow::describe_stale(entries, stale));
    let _ = writeln!(
        s,
        "{START}   Delete it. An entry that no longer matches anything is permission no commit \
         re-earns, and the next edit re-introduces the defect behind a clean gate."
    );
}
