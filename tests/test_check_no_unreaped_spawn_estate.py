"""The shell rules, and the shapes the ESTATE SWEEP found in the repos it found them in.

Four of `check_no_unreaped_spawn.py`'s first seven hits across twelve repos were CORRECT code, and
each was a MISSING PATTERN rather than a wrong one -- `try`/`finally`, a `Drop` that only waits, a
`Child` handed to a contractual reaper, a `#[cfg(test)]` region. These are their regression tests,
so the checker cannot go back to flagging the fix. (SUPERSOTA R3 in the only direction available to
a checker that has already been calibrated against the estate: the corpus tells you, and you either
fix the rule or record the exemption. Four rules were fixed; none was exempted.)

Split from `test_check_no_unreaped_spawn.py` at the 500-line cap, and the split is by PROVENANCE --
what this file is about is where each shape was found, which is the part a reader needs to weigh a
change against.
"""

from pathlib import Path

from unreaped_kit import findings

from conftest import commit_all, write

CHECK = "checks/check_no_unreaped_spawn.py"


# ── the shapes the ESTATE SWEEP found, in the repos it found them in ──────────
# Four of the checker's first seven hits across twelve repos were CORRECT code, and each was a
# missing pattern rather than a wrong one. These are the regression tests for those four, so the
# checker cannot go back to flagging the fix. (SUPERSOTA R3, in the only direction available to a
# checker that has already been calibrated: the corpus tells you, and you either fix the rule or
# record the exemption. Four rules were fixed; none was exempted.)


def test_python_try_finally_that_reaps_is_a_guard(repo: Path) -> None:
    """`tests/test_desktop_lock.py` and `tests/test_killtree.py`, verbatim in shape. The first
    version of this checker reported both as leaks."""
    write(
        repo,
        "tests/test_a.py",
        """\
import subprocess


def test_x():
    proc = subprocess.Popen(["x"])
    try:
        assert proc.pid > 0
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"try/finally is Python's Drop:\n{got.stdout}\n{got.stderr}"


def test_a_finally_that_restores_a_file_but_not_the_process_is_red(repo: Path) -> None:
    """`tests/test_release_hardening.py` as it stood, which was a REAL leak found in this repo's own
    suite: `release.sh --gate 'touch … && sleep 5'`, two asserts, then `communicate`, and a finally
    that restored the script and left the process running."""
    write(
        repo,
        "tests/test_a.py",
        """\
import subprocess


def test_x():
    proc = subprocess.Popen(["x"])
    try:
        assert proc.pid > 0
        out = proc.communicate(timeout=120)
    finally:
        SCRIPT.write_bytes(original)
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"{got.stdout}\n{got.stderr}"
    assert "reap-after-panic" in got.stdout + got.stderr


def test_a_popen_stored_on_self_or_returned_is_a_handoff(repo: Path) -> None:
    """`games/ZeroThunder`'s DemoServer and `gates_of_heck`'s own `_spawn()`. The reap belongs to
    whoever owns the object."""
    write(
        repo,
        "tests/test_a.py",
        """\
import subprocess


class DemoServer:
    def __init__(self):
        self.proc = subprocess.Popen(["x"])


def _spawn():
    return subprocess.Popen(["x"])
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"
    assert "2 handoff" in got.stdout + got.stderr, got.stdout


def test_a_drop_impl_that_only_waits_is_a_guard(repo: Path) -> None:
    """`media_server/crates/mediaops-rs/tests/it/backup_snapshot.rs`: `Drop for Holder` closes stdin
    and waits. Requiring kill AND wait called that a live orphan."""
    write(
        repo,
        "crates/x-rs/tests/it/a.rs",
        """\
use std::process::{Child, Command};

struct Holder {
    child: Child,
}

impl Holder {
    fn open() -> Self {
        let child = Command::new("sqlite3").spawn().expect("runs");
        assert!(child.stdin.is_some(), "panics; the Drop still reaps");
        Self { child }
    }
}

impl Drop for Holder {
    fn drop(&mut self) {
        drop(self.child.stdin.take());
        let _ = self.child.wait();
    }
}

#[test]
fn t() {
    let _h = Holder::open();
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"


def test_a_child_handed_to_a_contractual_reaper_is_clean(repo: Path) -> None:
    """`sys_updater`'s `wait_timeout(&mut child, ..)`. The helper reaps by contract; telling that
    author to stop using their own helper is the opposite of right."""
    write(
        repo,
        "crates/x-rs/tests/it/a.rs",
        """\
use std::process::{Command, Stdio};
use std::time::Duration;

#[test]
fn t() {
    let mut stuck = Command::new("sh")
        .args(["-c", "sleep 30"])
        .stdin(Stdio::null())
        .spawn()
        .unwrap();
    assert!(wait_timeout(&mut stuck, Duration::from_millis(200)).is_timed_out());
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"


def test_production_code_above_a_cfg_test_mod_is_out_of_scope(repo: Path) -> None:
    """`crates/goh/src/index_view.rs`: a `git checkout-index` spawn in production code, in a file
    that also carries a `#[cfg(test)] mod` at the bottom. The scope is REGION granular, which is how
    the compiler answers it."""
    write(
        repo,
        "src/index_view.rs",
        """\
use std::io::Write;
use std::process::Command;

pub fn checkout_index(top: &str) -> Result<(), String> {
    let mut child = Command::new("git").arg("checkout-index").spawn()?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(b"")?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    #[test]
    fn t() {
        assert!(true);
    }
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, (
        f"a file-granular scope read production code as a test:\n{got.stdout}"
    )


def test_an_unreaped_spawn_inside_the_cfg_test_mod_is_still_red(repo: Path) -> None:
    """The other direction: narrowing the scope must not blind it."""
    write(
        repo,
        "src/index_view.rs",
        """\
#[cfg(test)]
mod tests {
    use std::process::Command;
    #[test]
    fn t() {
        let _c = Command::new("x").spawn().unwrap();
        assert!(true);
    }
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"{got.stdout}\n{got.stderr}"


def test_a_deadline_watchdog_is_reported_and_counted_not_failed(repo: Path) -> None:
    """`media_server`'s lan-ip watcher arms a thread that SIGKILLs the child after 30 s. Bounded is
    not prevented, so it is a real mitigation -- and it is COUNTED, because a mitigation nobody can
    see is a mitigation that decays."""
    write(
        repo,
        "crates/x-rs/tests/a.rs",
        """\
use std::process::Command;
use std::time::Duration;

#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    let pid = child.id() as i32;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(30));
        unsafe { libc::kill(pid, libc::SIGKILL) };
    });
    assert!(child.id() > 0);
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"
    assert "1 watchdog" in got.stdout + got.stderr, got.stdout
