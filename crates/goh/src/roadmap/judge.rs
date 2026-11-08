//! The ratchet over a roadmap's findings: `roadmap_seed.json`, a per-`<file>#<rule>` count that
//! may only fall (close-the-class).
//!
//! The four directions, each a unit test: a key over its count is red with its sites; a key under
//! its count is red until the seed is lowered, so a pruned entry leaves no room for a new one; a
//! seeded key whose findings are gone is STALE and red; a corrupt seed is an error, never an empty
//! seed. And the fifth, the one a re-seed would otherwise launder: the seed itself may not GROW
//! against the one HEAD carries -- a new violation is fixed, never seeded. A key the run could not
//! judge (the queue rule with no queue) is withheld on both sides, so a blind rule does not read
//! as a fixed one.

use goh_tui::{ERR, OK, START, WARN};
use std::collections::BTreeMap;
use std::fmt::Write as _;

use super::{SEED_FILE, TAG};

/// What one run found, before the seed is consulted.
#[derive(Debug, Default)]
pub struct Gathered {
    /// The declared roadmap, repo-relative.
    pub roadmap: String,
    /// Every finding's site (`path:line: what`), by seed key (`<path>#<rule>`).
    pub found: BTreeMap<String, Vec<String>>,
    /// Findings no seed may hold: a declared roadmap that is not there.
    pub hard: Vec<String>,
    /// Advisories, printed on a pass too: what the run could not judge, and why.
    pub notes: Vec<String>,
    /// The `queue-done` rule was not judged (no queue): its seed entries are withheld.
    pub queue_withheld: bool,
    pub entries: usize,
    pub phases: usize,
}

/// A seed key the run did not judge.
fn withheld(g: &Gathered, key: &str) -> bool {
    g.queue_withheld && key.ends_with("#queue-done")
}

/// The verdict: (exit code, report). `head` is the seed HEAD carries, if HEAD carries one.
#[must_use]
pub fn judge(
    g: &Gathered,
    seed: &BTreeMap<String, usize>,
    head: Option<&BTreeMap<String, usize>>,
) -> (i32, String) {
    let mut s = String::new();
    for note in &g.notes {
        let _ = writeln!(s, "{WARN} {TAG} {note}");
    }
    let mut red = false;
    for hard in &g.hard {
        red = true;
        let _ = writeln!(s, "{ERR} {TAG} {hard}");
    }
    if let Some(head) = head {
        for (key, &n) in seed {
            let was = head.get(key).copied().unwrap_or(0);
            if n > was {
                red = true;
                let _ = writeln!(
                    s,
                    "{ERR} {TAG} {SEED_FILE}: {key} rose from {was} to {n}: the seed only falls \
                     -- fix the finding instead of seeding it"
                );
            }
        }
    }
    for (key, sites) in &g.found {
        let allowed = seed.get(key).copied().unwrap_or(0);
        if sites.len() > allowed {
            red = true;
            for site in sites {
                let _ = writeln!(s, "{ERR} {TAG} {site}");
            }
            if allowed > 0 {
                let _ = writeln!(
                    s,
                    "  ({key} is seeded with {allowed}; it has {})",
                    sites.len()
                );
            }
        }
    }
    for (key, &allowed) in seed {
        let has = g.found.get(key).map_or(0, Vec::len);
        if has < allowed && !withheld(g, key) {
            red = true;
            let _ = writeln!(
                s,
                "{ERR} {TAG} {key} is seeded with {allowed} and has {has}: lower the seed \
                 (goh roadmap --seed), so a pruned entry leaves no room for a new one"
            );
        }
    }
    if red {
        let _ = writeln!(
            s,
            "{START} {TAG} {}: one forward-looking file, open entries only, each with an id and a \
             `Done when:` (gates_of_heck docs/map.md, `goh roadmap`)",
            g.roadmap
        );
        return (1, s);
    }
    let seeded: usize = seed.values().sum();
    let _ = writeln!(
        s,
        "{OK} {TAG} {}: {} open entr(ies) in {} phase(s), {seeded} seeded",
        g.roadmap, g.entries, g.phases
    );
    (0, s)
}

/// Read a seed's text: malformed is an ERROR, never an empty seed.
///
/// # Errors
///
/// What is wrong with the text.
pub fn parse_seed(text: &str) -> Result<BTreeMap<String, usize>, String> {
    let value: serde_json::Value =
        serde_json::from_str(text).map_err(|e| format!("{SEED_FILE} is not JSON: {e}"))?;
    let entries = value
        .get("entries")
        .and_then(serde_json::Value::as_object)
        .ok_or_else(|| format!("{SEED_FILE} has no \"entries\" object"))?;
    entries
        .iter()
        .map(|(k, v)| {
            v.as_u64()
                .and_then(|n| usize::try_from(n).ok())
                .map(|n| (k.clone(), n))
                .ok_or_else(|| format!("{SEED_FILE}: {k}'s count is not a whole number"))
        })
        .collect()
}

/// The seed this run implies. A withheld key keeps the count `old` had, so seeding on a machine
/// with no queue neither drops nor invents the queue rule's debt.
///
/// # Errors
///
/// A hard finding stands (nothing to seed against), or a key would rise above `old`'s count
/// when there is an `old`: the seed only falls.
pub fn seed_text(g: &Gathered, old: Option<&BTreeMap<String, usize>>) -> Result<String, String> {
    if let Some(hard) = g.hard.first() {
        return Err(format!("refusing to seed: {hard}"));
    }
    let mut counts: BTreeMap<String, usize> =
        g.found.iter().map(|(k, v)| (k.clone(), v.len())).collect();
    if let Some(old) = old {
        for (key, &n) in old {
            if withheld(g, key) {
                counts.insert(key.clone(), n);
            }
        }
        let rose: Vec<String> = counts
            .iter()
            .filter(|(k, n)| **n > old.get(*k).copied().unwrap_or(0))
            .map(|(k, n)| format!("{k} ({} -> {n})", old.get(k).copied().unwrap_or(0)))
            .collect();
        if !rose.is_empty() {
            return Err(format!(
                "refusing to seed: the seed only falls, and {} would rise: {}",
                rose.len(),
                rose.join(", ")
            ));
        }
    }
    let entries: serde_json::Map<String, serde_json::Value> = counts
        .into_iter()
        .map(|(k, n)| (k, serde_json::Value::from(n)))
        .collect();
    let doc = serde_json::json!({
        "why": "the roadmap's findings when this repo adopted goh roadmap; a count may only fall -- \
                prune completed work to git and the CHANGELOG, give every open entry an id and a \
                `Done when:`, keep one plan file (house rule #16, GOH.ROADMAPGATE)",
        "roadmap": g.roadmap,
        "entries": entries,
    });
    Ok(serde_json::to_string_pretty(&doc).unwrap_or_default() + "\n")
}
