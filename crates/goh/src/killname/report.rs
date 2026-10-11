//! The kill-by-name report: which kills are excused, which entries are stale and why, and which
//! entries matched only part of their call (`crate::callkey`).

use std::collections::BTreeSet;
use std::fmt::Write as _;

use super::{Entry, Hit, ALLOW_FILE, TAG};
use crate::callkey as key;

/// Each hit against the entries: (the entries used, the hits none excuses, the hits excused only
/// by a fragment of their call).
type Judged<'a> = (BTreeSet<usize>, Vec<&'a Hit>, Vec<(&'a Entry, &'a Hit)>);

fn judge<'a>(hits: &'a [Hit], entries: &'a [Entry]) -> Judged<'a> {
    let mut used: BTreeSet<usize> = BTreeSet::new();
    let mut left: Vec<&Hit> = Vec::new();
    let mut rekey: Vec<(&Entry, &Hit)> = Vec::new();
    for hit in hits {
        let fits = entries.iter().enumerate().filter_map(|(i, e)| {
            (e.path == hit.0)
                .then(|| key::fit(&e.line, &hit.2, hit.4.as_deref()))
                .flatten()
                .map(|f| (i, f))
        });
        // An exact entry outranks a fragment, so one call never reads as a guess when it has a key.
        match fits.min_by_key(|&(_, f)| f != key::Fit::Exact) {
            Some((i, f)) => {
                used.insert(i);
                if f == key::Fit::Fragment {
                    rekey.push((&entries[i], hit));
                }
            }
            None => left.push(hit),
        }
    }
    (used, left, rekey)
}

/// The reference's report: (exit code, stdout).
pub fn report(
    staged: bool,
    hits: &[Hit],
    checked: usize,
    entries: &[Entry],
    scanned: &BTreeSet<&str>,
) -> (i32, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let (used, left, rekey) = judge(hits, entries);
    let stale: Vec<&Entry> = entries
        .iter()
        .enumerate()
        .filter(|(i, e)| !used.contains(i) && scanned.contains(e.path.as_str()))
        .map(|(_, e)| e)
        .collect();
    let mut s = String::new();
    if !left.is_empty() {
        let _ = writeln!(
            s,
            "✗ {TAG} {} process kill(s) by name in {scope} code:",
            left.len()
        );
        for (rel, lineno, code, why, _) in left.iter().take(40) {
            let shown: String = code.chars().take(140).collect();
            let _ = writeln!(s, "    {rel}:{lineno}: {shown}  ({why})");
        }
        s.push_str(
            "    kill what you started: its pid, its process group (pkill -g, killpg), or the\n",
        );
        s.push_str(
            "    tree below your own pid (pkill -P). A name matches processes you do not own.\n",
        );
        let _ = writeln!(
            s,
            "    A kill that must stand goes in {ALLOW_FILE} with a reason."
        );
    }
    if !stale.is_empty() {
        let _ = writeln!(
            s,
            "✗ {TAG} {} stale entr(ies) in {ALLOW_FILE}: no kill matches them any more:",
            stale.len()
        );
        for e in &stale {
            // Two causes, two remedies: the call changed (judge the new one) or it is gone.
            let why = left.iter().find(|h| h.0 == e.path).map_or_else(
                || "the kill is gone: delete the entry".to_owned(),
                |h| {
                    format!(
                        "{} still kills by name, at line {}, in a different call: judge that one",
                        e.path, h.1
                    )
                },
            );
            let _ = writeln!(s, "    {}: {}  ({why})", e.path, e.line);
        }
    }
    if !left.is_empty() || !stale.is_empty() {
        return (1, s);
    }
    if checked == 0 {
        if staged {
            let _ = writeln!(s, "✓ {TAG} nothing staged — 0 code files to check");
            return (0, s);
        }
        let _ = writeln!(
            s,
            "✗ {TAG} nothing to check (tracked) — refusing to report clean over zero code files"
        );
        return (1, s);
    }
    let note = if hits.is_empty() {
        String::new()
    } else {
        format!(", {} allowlisted in {ALLOW_FILE}", hits.len())
    };
    let _ = writeln!(
        s,
        "✓ {TAG} OK — {checked} {scope} code files, no kill by name{note}"
    );
    for (e, hit) in &rekey {
        let call: String = hit
            .4
            .as_deref()
            .unwrap_or(&hit.2)
            .chars()
            .take(140)
            .collect();
        let _ = writeln!(
            s,
            "⚠ {TAG}   {}:{}: the entry is part of the call; key it on the whole call: {call}",
            e.path, hit.1
        );
    }
    let debt = used.iter().filter(|i| entries[**i].unreviewed).count();
    if debt > 0 {
        let _ = writeln!(
            s,
            "⚠ {TAG}   {debt} of them 'unreviewed': found when the gate was seeded, not yet decided"
        );
    }
    (0, s)
}
