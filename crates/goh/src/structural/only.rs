//! `goh structural --only STEP [--only STEP]...` (GOH.SELFWALKFILTER): the run narrowed to named
//! steps.
//!
//! The pipeline is one serial run of every step, which a caller that judges every step one by one
//! (the self-walk, `tests/test_self_walk.py`) could not split: 52 steps, one process, ~33 s, and no
//! parallel runner can spread one process. Named here, a step runs; unnamed, it is skipped before
//! it does any work. A name the binary does not carry (`structural_steps.txt`) is REFUSED by name:
//! a typo that ran nothing would print the pass line over a tree nobody judged.
//!
//! One step per flag, never a comma list: a carried name holds a comma (`no temp, lock or stage
//! path in a work tree`), and the self-walk's first split run refused it as two unknown steps.
//!
//! A step function that announces two steps (the line-cap ceiling pair, the empty-tree refusal and
//! the self-proofs) runs whole when either is named. A step `structural_steps.txt` does not list
//! (the staged-only Rust policies, `GOH_INSTALLS`) is dispatched with no name, so it runs only in
//! an unnarrowed run. Every name dispatched is a carried step, and every carried step is
//! dispatched: `pipeline::tests::every_carried_step_is_dispatched_once`.

use std::collections::BTreeSet;

/// The steps a run is narrowed to; `None` runs every step.
pub(super) struct Only(Option<BTreeSet<String>>);

impl Only {
    /// Every step.
    #[cfg(test)]
    pub(super) const fn all() -> Self {
        Self(None)
    }

    /// The `--only` values, one step each, checked against the carried steps.
    ///
    /// # Errors
    /// The refusal text, naming every value that is no carried step.
    pub(super) fn parse(values: &[String], carried: &[&str]) -> Result<Self, String> {
        if values.is_empty() {
            return Ok(Self(None));
        }
        let names: BTreeSet<String> = values.iter().map(|v| v.trim().to_owned()).collect();
        let unknown: Vec<&str> = names
            .iter()
            .map(String::as_str)
            .filter(|n| !carried.contains(n))
            .collect();
        if !unknown.is_empty() {
            return Err(format!(
                "structural: --only names {} this binary does not carry: {}\n  → \
                 `goh structural --list-steps` prints the steps it carries",
                goh_tui::noun_as(unknown.len(), "a step", "steps"),
                unknown
                    .iter()
                    .map(|n| format!("`{n}`"))
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
        Ok(Self(Some(names)))
    }

    /// Whether a step carrying `names` runs: always unnarrowed, else when any of them was named.
    pub(super) fn wants(&self, names: &[&str]) -> bool {
        self.0
            .as_ref()
            .is_none_or(|set| names.iter().any(|n| set.contains(*n)))
    }

    /// `step()`'s outcome when `names` runs, and no outcome (no work done) when it does not.
    pub(super) fn run(&self, names: &[&str], step: impl FnOnce() -> Option<i32>) -> Option<i32> {
        if self.wants(names) {
            step()
        } else {
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Only;

    const CARRIED: &[&str] = &[
        "file length",
        "shell lint",
        "no conflict markers",
        "no temp, lock or stage path in a work tree",
    ];

    fn parse(values: &[&str]) -> Result<Only, String> {
        let values: Vec<String> = values.iter().map(|v| (*v).to_owned()).collect();
        Only::parse(&values, CARRIED)
    }

    #[test]
    fn no_value_runs_every_step_and_a_nameless_step_too() {
        let only = parse(&[]).expect("parses");
        assert!(only.wants(&["file length"]) && only.wants(&[]));
        assert!(Only::all().wants(&["shell lint"]));
    }

    #[test]
    fn named_steps_run_and_the_rest_do_no_work() {
        let only = parse(&["file length", " shell lint "]).expect("parses");
        assert!(only.wants(&["file length"]) && only.wants(&["no conflict markers", "shell lint"]));
        assert!(!only.wants(&["no conflict markers"]) && !only.wants(&[]));
        let mut ran = false;
        assert_eq!(
            only.run(&["no conflict markers"], || {
                ran = true;
                Some(1)
            }),
            None
        );
        assert!(!ran, "a step not named did its work");
        assert_eq!(only.run(&["shell lint"], || Some(1)), Some(1));
    }

    #[test]
    fn a_name_holding_a_comma_is_one_step_never_two() {
        let only = parse(&["no temp, lock or stage path in a work tree"]).expect("parses");
        assert!(only.wants(&["no temp, lock or stage path in a work tree"]));
        assert!(!only.wants(&["file length"]));
    }

    #[test]
    fn an_unknown_or_empty_name_is_refused_by_name() {
        let why = parse(&["file length,shell lint", "file length", "nor this", ""])
            .err()
            .expect("refused");
        assert!(
            why.contains("`file length,shell lint`") && why.contains("`nor this`"),
            "{why}"
        );
        assert!(
            why.contains("``") && !why.contains("`file length`"),
            "{why}"
        );
        assert!(why.contains("--list-steps"), "{why}");
    }
}
