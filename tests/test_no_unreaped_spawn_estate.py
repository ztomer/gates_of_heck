"""The estate findings, as TESTS rather than table rows: a rule proved by a real repo shape.

    tests/test_no_unreaped_spawn_estate.py          # run with pytest

Split out of `test_check_no_unreaped_spawn.py` at the 500-line cap. The cut is at a real
boundary: the table in `checks/_unreaped_spawn_table_regressions.py` pins the RULES, and
these pin the two things a table cannot -- that a guard defined in ANOTHER file still
protects this one, and that crate scope does not lend credence to a local `Drop` that says
it does not reap. Both are whole-repo behaviours: they need two files, which a one-file
fixture cannot express, and the failure they guard is a FALSE ALARM on correct code.
"""

import subprocess

from conftest import Path, commit_all, run_check, write

CHECK = "checks/check_no_unreaped_spawn.py"


def findings(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return run_check(repo, CHECK, *args)


def test_a_guard_defined_in_another_test_file_is_still_a_guard(repo: Path) -> None:
    """A guard defined ONCE in `tests/common/` protects every file that uses it.

    Found by the estate sweep on `routines`, whose `ReapOnDrop` lives in
    `tests/lifecycle_support/mod.rs` and is used by three test files. Read per file, the guard is
    invisible and the two sites that guard FIXES are reported as unjudgeable -- a false alarm on the
    exact code the guard was written for, in the exact files the bug report named.
    """
    write(
        repo,
        "crates/x-rs/tests/common/mod.rs",
        """\
use std::process::Child;

pub struct ReapOnDrop(pub Child);

impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
""",
    )
    write(
        repo,
        "crates/x-rs/tests/test_shared.rs",
        """\
mod common;

use std::process::Command;

use common::ReapOnDrop;

#[test]
fn t() {
    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));
    assert!(child.0.id() > 0, "panics; the guard reaps on unwind");
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"a shared guard was refused:\n{got.stdout}\n{got.stderr}"


def test_an_empty_drop_in_another_file_does_not_launder_a_local_one(repo: Path) -> None:
    """Crate scope must not CREDENCE a type whose LOCAL `Drop` says it does not reap.

    The other direction of the same fix, and the reason the local set wins: `tests/common/` declares
    `Holder` with a `Drop` that kills and waits, and the file under test declares a DIFFERENT
    `Holder` whose `Drop` is empty. The compiler uses the local one, so the local one decides.
    """
    write(
        repo,
        "crates/x-rs/tests/common/mod.rs",
        """\
use std::process::Child;

pub struct Holder(pub Child);

impl Drop for Holder {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
""",
    )
    write(
        repo,
        "crates/x-rs/tests/test_shadowed.rs",
        """\
use std::process::{Child, Command};

struct Holder(Child);

impl Holder {
    fn spawn(command: &mut Command) -> Self {
        Self(command.spawn().unwrap())
    }
}

impl Drop for Holder {
    fn drop(&mut self) {
    }
}

#[test]
fn t() {
    let holder = Holder::spawn(Command::new("sh").arg("-c").arg("exec sleep 30"));
    panic!("the failing write_all lands here");
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    out = got.stdout + got.stderr
    assert got.returncode == 1, f"an empty Drop was accepted as a guard:\n{out}"
    assert "neither kills nor waits" in out, out


def test_the_mandatory_stuff_the_sweep_found(repo: Path) -> None:
    """The two ZeroThunder shapes: a `return` above the reap, and a `try` ABOVE the spawn.

    Both are the real files from `games/ZeroThunder`, and the first is the one site the estate still
    carries unfixed. It was invisible because Rust's panic scan has always counted a bare `return` as
    an exit that skips the reap and Python's never did -- an asymmetry between two halves of one
    rule, found by reading a site this gate could not see rather than by reading the rule.
    """
    write(
        repo,
        "tests/e2e/garden_drag_flicker.py",
        """\
import subprocess


def run():
    proc = subprocess.Popen(["x", "--seed", "42", "--debug"], stdout=None, stderr=None)
    if not wait_for_app(timeout=15):
        print("socket down")
        return 2
    proc.terminate()
    return 0
""",
    )
    write(
        repo,
        "tests/e2e/wrapped.py",
        """\
import subprocess


def run():
    try:
        proc = subprocess.Popen(["x"])
        if not ready():
            return 2
        check()
        return 0
    finally:
        if proc:
            proc.terminate()
""",
    )
    commit_all(repo)
    got = findings(repo)
    out = got.stdout + got.stderr
    assert got.returncode == 1, f"an early return above the reap was accepted:\n{out}"
    assert "tests/e2e/garden_drag_flicker.py:5" in out, out
    assert "tests/e2e/wrapped.py" not in out, f"a try/finally around the spawn was refused:\n{out}"
