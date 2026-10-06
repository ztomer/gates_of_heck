//! Run the delegated checkers concurrently; report them in declared order (BACKLOG P1e).
//!
//! Measured 2026-10-05: `structural --full` on `media_server` spent 4.7 of its 4.9 s in the
//! delegated Python checkers, one after another, while each waited mostly on its own interpreter
//! start and tree read. They are independent and read-only, so they now START together and are
//! REPORTED in sequence: every `· label` / `✓ label` line, the first failure and its tail come out
//! exactly as the serial pipeline printed them (`tests/test_goh_structural.py` pins it).
//!
//! How the set is found without a second list of conditions to drift: `collect` runs the same
//! delegated step functions in a mode where `delegated()` records its command instead of running
//! it, so the prefetch is exactly what the sequence would run. A step missing from the collect
//! pass is merely run synchronously; nothing is skipped.
//!
//! Nothing outlives the run: `Drain` joins every checker still running when the gate returns,
//! including the fail-fast return after a red step -- a gate that exits and leaves children behind
//! is the defect class `check_no_unreaped_spawn.py` exists for.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;

type Outcome = (i32, String);
type Spec = (String, Vec<String>);

static COLLECTING: Mutex<Option<Vec<Spec>>> = Mutex::new(None);
static PENDING: Mutex<Option<HashMap<String, JoinHandle<Outcome>>>> = Mutex::new(None);

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn key(program: &str, args: &[String]) -> String {
    let mut k = program.to_owned();
    for a in args {
        k.push('\0');
        k.push_str(a);
    }
    k
}

/// The commands `f` would delegate, recorded and not run.
pub(crate) fn collect(f: impl FnOnce()) -> Vec<Spec> {
    *lock(&COLLECTING) = Some(Vec::new());
    f();
    lock(&COLLECTING).take().unwrap_or_default()
}

/// In a `collect` pass, record this command and say so; otherwise false.
pub(crate) fn record_if_collecting(program: &str, args: &[String]) -> bool {
    lock(&COLLECTING).as_mut().is_some_and(|specs| {
        specs.push((program.to_owned(), args.to_vec()));
        true
    })
}

/// Start every collected command now, each on its own thread.
pub(crate) fn start(specs: Vec<Spec>, checks: &Path, repo: &Path) {
    let mut started = Vec::new();
    for (program, args) in specs {
        let k = key(&program, &args);
        if started.iter().any(|(seen, _)| *seen == k) {
            continue;
        }
        let (checks, repo): (PathBuf, PathBuf) = (checks.to_path_buf(), repo.to_path_buf());
        let handle = std::thread::spawn(move || {
            crate::step_report::run_child(&program, &args, &checks, &repo)
        });
        started.push((k, handle));
    }
    lock(&PENDING)
        .get_or_insert_with(HashMap::new)
        .extend(started);
}

/// The result of an already-started command, waiting for it; `None` if it was never started.
pub(crate) fn take(program: &str, args: &[String]) -> Option<Outcome> {
    let handle = lock(&PENDING).as_mut()?.remove(&key(program, args))?;
    Some(
        handle
            .join()
            .unwrap_or_else(|_| (1, "the checker's runner thread panicked\n".to_owned())),
    )
}

/// Joins every started command on drop, so no checker outlives the gate.
pub(crate) struct Drain;

impl Drop for Drain {
    fn drop(&mut self) {
        let left: Vec<JoinHandle<Outcome>> = lock(&PENDING)
            .as_mut()
            .map(|map| map.drain().map(|(_, h)| h).collect())
            .unwrap_or_default();
        for handle in left {
            let _ = handle.join();
        }
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn collected_commands_start_together_and_are_taken_once() {
        let specs = super::collect(|| {
            assert!(super::record_if_collecting("true", &["a".to_owned()]));
            assert!(super::record_if_collecting("true", &["b".to_owned()]));
        });
        assert_eq!(specs.len(), 2);
        assert!(
            !super::record_if_collecting("true", &["c".to_owned()]),
            "outside collect"
        );
        let dir = tempfile::tempdir().expect("tempdir");
        let drain = super::Drain;
        super::start(specs, dir.path(), dir.path());
        let (code, _) = super::take("true", &["a".to_owned()]).expect("a was started");
        assert_eq!(code, 0);
        assert!(
            super::take("true", &["a".to_owned()]).is_none(),
            "taken once"
        );
        drop(drain); // joins `b`, which nobody took
        assert!(super::take("true", &["b".to_owned()]).is_none(), "drained");
    }
}
