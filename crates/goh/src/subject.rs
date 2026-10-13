//! Every scanner declares its subject, so a scan that stopped reading says so (BACKLOG 4.3).
//!
//! THE CLASS, in one line: a scanner whose own selection broke reads less than the repo holds, and
//! a scan of nothing finds nothing, so it reports clean. It was found independently in four repos
//! -- an `__all__` omitting a name, a gate regex dropping gates declared with a third tuple element,
//! and a regex `ruff format` broke, which silently vanished three gates from a calibration -- and
//! every time the gate's output read exactly like a pass.
//!
//! THE SHAPE, one key per scanner, `GOH_<SCANNER>_MIN_<UNIT>` in `.gatesrc` (the shape
//! `GOH_SUBPROCESS_STDIN_MIN_CALLS` had alone):
//!
//!   * unset: no floor, and the structural gate SAYS so at full scope -- one line naming every
//!     scanner that ran with no floor and what it read this run, ready to paste;
//!   * not a whole number: `gatesrc::load` refuses the file naming the key (exit 2), as it refuses
//!     a bad `GOH_MAX_LINES` -- never a floor of zero, because a typo that silently disables the
//!     floor is the defect the floor exists to prevent;
//!   * `0`: a DECLARED "no floor here" (a repo with no shell, say), which silences the advisory;
//!   * below the floor: red, naming the key, the floor and the count read ("read NONE" when the
//!     count is zero, which is what an exclusion covering the whole subject looks like).
//!
//! FULL SCOPE ONLY. At `--staged` a scanner reads the files this commit brings, so a floor would
//! refuse every small commit for its own size; the scanners still record, and nothing judges.
//!
//! THE PAIRING with a seed or allowlist's stale-entry check, in both directions. A stale entry (a
//! seeded finding that is gone) is how a scan that stopped seeing FINDINGS shows; a floor is how a
//! scan that stopped seeing its SUBJECT shows. (1) Below the floor, the stale verdict is withheld
//! and `--seed` refuses: a blind scan finds nothing, so its stale entries would tell the owner to
//! lower the seed, and the re-seed would launder the blindness into a smaller ratchet
//! (`crate::seedcount`, `goh subprocess-stdin`). (2) At or above the floor, stale entries still
//! fail: a floor counts the subject as a whole and cannot vouch for one file's findings, and a
//! floor set too low never fires, while the stale check still catches the scanner that lost the
//! files carrying findings. A floor set too HIGH reds a clean repo and is lowered with the tree in
//! the same commit, so the change is reviewed.
//!
//! The scanners record what they read here ([`record`]); the structural pipeline's last step
//! ([`step`]) judges every record against the repo's floors. A thread-local, not a parameter
//! threaded through every step: the steps run in order on the pipeline's thread, so each run --
//! and each test, which the harness gives its own thread -- sees only its own records.

use goh_tui::{ERR, START};
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fmt::Write as _;

/// One scanner's subject: the key that holds its floor and what it counts.
#[derive(Debug, PartialEq, Eq)]
pub struct Subject {
    /// The `.gatesrc` key holding this scanner's floor.
    pub key: &'static str,
    /// What the count counts, plural, e.g. `text file(s)`.
    pub unit: &'static str,
    /// The structural step (or command) that reads it.
    pub scanner: &'static str,
}

pub const EMOJI: Subject = Subject {
    key: "GOH_EMOJI_MIN_FILES",
    unit: "text file(s)",
    scanner: "no disallowed emoji",
};
pub const CONFLICT_MARKERS: Subject = Subject {
    key: "GOH_CONFLICT_MARKERS_MIN_FILES",
    unit: "file(s)",
    scanner: "no conflict markers",
};
pub const FILE_LENGTH: Subject = Subject {
    key: "GOH_FILE_LENGTH_MIN_FILES",
    unit: "file(s) measured",
    scanner: "file length",
};
pub const SECRETS: Subject = Subject {
    key: "GOH_SECRETS_MIN_FILES",
    unit: "text file(s)",
    scanner: "no committed secrets",
};
pub const SHELL_LINT: Subject = Subject {
    key: "GOH_SHELL_LINT_MIN_FILES",
    unit: "shell file(s)",
    scanner: "shell lint",
};
pub const EARLY_EXIT_PIPE: Subject = Subject {
    key: "GOH_EARLY_EXIT_PIPE_MIN_FILES",
    unit: "shell source(s)",
    scanner: "no early-exit pipe under pipefail",
};
pub const DEAD_AFTER_EXEC: Subject = Subject {
    key: "GOH_DEAD_AFTER_EXEC_MIN_FILES",
    unit: "shell source(s)",
    scanner: "no code after exec",
};
pub const BARE_HOOK_INDEX: Subject = Subject {
    key: "GOH_BARE_HOOK_INDEX_MIN_FILES",
    unit: "shell source(s)",
    scanner: "no bare read of the hook's index",
};
pub const BARE_PYTHON: Subject = Subject {
    key: "GOH_BARE_PYTHON_MIN_FILES",
    unit: "hook/gate script(s)",
    scanner: "no bare python in a hook or gate",
};
pub const CHECKOUT_CREDENTIALS: Subject = Subject {
    key: "GOH_CHECKOUT_CREDENTIALS_MIN_FILES",
    unit: "workflow/action file(s)",
    scanner: "no checkout leaves its token in git config",
};
pub const MD_LINKS: Subject = Subject {
    key: "GOH_MD_LINKS_MIN_LINKS",
    unit: "markdown link(s)",
    scanner: "markdown links resolve",
};
pub const UNREAPED_SPAWN: Subject = Subject {
    key: "GOH_UNREAPED_SPAWN_MIN_FILES",
    unit: "test file(s)",
    scanner: "no unreaped spawns in tests",
};
pub const SUBPROCESS_STDIN: Subject = Subject {
    key: "GOH_SUBPROCESS_STDIN_MIN_CALLS",
    unit: "process call(s)",
    scanner: "every child process names its stdin",
};
pub const FIXED_SLEEP: Subject = Subject {
    key: "GOH_FIXED_SLEEP_MIN_FILES",
    unit: "Python file(s)",
    scanner: "goh fixed-sleep",
};
pub const STATUS_MARKERS: Subject = Subject {
    key: "GOH_STATUS_MARKERS_MIN_FILES",
    unit: "source file(s)",
    scanner: "goh status-markers",
};

/// Every scanner with a subject, spelled once: the keys `.gatesrc` may hold a floor in.
pub const ALL: [&Subject; 15] = [
    &EMOJI,
    &CONFLICT_MARKERS,
    &FILE_LENGTH,
    &SECRETS,
    &SHELL_LINT,
    &EARLY_EXIT_PIPE,
    &DEAD_AFTER_EXEC,
    &BARE_HOOK_INDEX,
    &BARE_PYTHON,
    &CHECKOUT_CREDENTIALS,
    &MD_LINKS,
    &UNREAPED_SPAWN,
    &SUBPROCESS_STDIN,
    &FIXED_SLEEP,
    &STATUS_MARKERS,
];

/// Every floor `.gatesrc` declares, parsed. Absent and empty keys are absent; a value that is not
/// a whole number is an error NAMING THE KEY.
///
/// # Errors
///
/// The first floor key whose value is not a whole number.
pub fn parse_floors(
    pairs: &BTreeMap<String, String>,
) -> Result<BTreeMap<&'static str, usize>, String> {
    let mut floors = BTreeMap::new();
    for subject in ALL {
        // Present but EMPTY is not a floor, as an empty opt-in is not a choice: the advisory
        // still names the key.
        let Some(raw) = pairs.get(subject.key).filter(|v| !v.trim().is_empty()) else {
            continue;
        };
        let n = raw.trim().parse::<usize>().map_err(|_| {
            format!(
                "{} is not a whole number of {}: {raw:?} -- a floor that does not parse is \
                 refused, never read as no floor",
                subject.key, subject.unit
            )
        })?;
        floors.insert(subject.key, n);
    }
    Ok(floors)
}

/// The floor `cfg` declares for `subject`, if any.
#[must_use]
pub fn floor(cfg: &crate::gatesrc::Gatesrc, subject: &Subject) -> Option<usize> {
    cfg.floors.get(subject.key).copied()
}

/// The stopped-reading verdict when `read` is under `floor`, else `None`. One wording for every
/// scanner, so a reader learns it once.
#[must_use]
pub fn below(subject: &Subject, floor: Option<usize>, read: usize) -> Option<String> {
    let floor = floor.filter(|f| read < *f)?;
    let what = if read == 0 {
        format!("read NONE of its subject ({})", subject.unit)
    } else {
        format!("read {read} {}", subject.unit)
    };
    Some(format!(
        "{ERR} [subject] `{}` {what} and .gatesrc declares a floor of {floor} ({})\n\
         {START}   A scan that has stopped reading its subject reads as clean. Find what moved \
         first -- an exclusion, a file filter, a parser -- or lower {} with the tree in the same \
         commit, so the change is reviewed.\n",
        subject.scanner, subject.key, subject.key
    ))
}

thread_local! {
    /// What each scanner read in this pipeline run, by key: one entry per scanner, the last record
    /// winning.
    static READ: RefCell<BTreeMap<&'static str, (&'static Subject, usize)>> =
        const { RefCell::new(BTreeMap::new()) };
}

/// A scanner reports how much of its subject it read.
pub fn record(subject: &'static Subject, read: usize) {
    READ.with(|r| r.borrow_mut().insert(subject.key, (subject, read)));
}

/// Every record so far, in registry order, and the records cleared.
#[must_use]
pub fn take() -> Vec<(&'static Subject, usize)> {
    let mut got = READ.with(|r| std::mem::take(&mut *r.borrow_mut()));
    ALL.iter().filter_map(|s| got.remove(s.key)).collect()
}

/// The verdict over every record: (exit code, text). Red names each scanner under its floor; a
/// pass carries the advisory line when any scanner that read something has no floor. Pure.
#[must_use]
pub fn judge(
    records: &[(&Subject, usize)],
    floors: &BTreeMap<&'static str, usize>,
) -> (i32, String) {
    let mut red = String::new();
    let mut unset = Vec::new();
    for (subject, n) in records {
        match floors.get(subject.key) {
            None if *n > 0 => unset.push(format!("{}={n}", subject.key)),
            floor => red.push_str(&below(subject, floor.copied(), *n).unwrap_or_default()),
        }
    }
    if !red.is_empty() {
        return (1, red);
    }
    let mut s = String::new();
    if !unset.is_empty() {
        let _ = writeln!(
            s,
            "{START} [subject] {} scanner(s) declare no floor in .gatesrc, so a scan that stops \
             reading would pass; this run read: {}",
            unset.len(),
            unset.join(" ")
        );
    }
    (0, s)
}

/// The step's label, as `structural_steps.txt` names it.
pub const LABEL: &str = "every scanner read its declared subject";

/// The structural step, last in the pipeline: every scanner's record against its floor. Full
/// scope only; at `--staged` the records are dropped unjudged.
#[must_use]
pub fn step(cfg: &crate::gatesrc::Gatesrc, staged: bool) -> Option<i32> {
    let read = take();
    if staged {
        return None;
    }
    let start = crate::step_report::begin(LABEL);
    let (code, text) = judge(&read, &cfg.floors);
    if code == 0 {
        goh_tui::out(&text);
        crate::step_report::ok(LABEL, start);
        return None;
    }
    Some(crate::step_report::fail(
        LABEL,
        &crate::step_report::ported("crates/goh/src/subject.rs"),
        &text,
        start,
    ))
}

#[cfg(test)]
#[path = "subject_tests.rs"]
mod tests;
