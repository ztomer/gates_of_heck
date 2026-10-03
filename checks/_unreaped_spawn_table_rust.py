"""The RUST half of the measured table: one row per disposition, each a real shape.

Split from `_unreaped_spawn_table.py` at the 500-line cap. It is the largest table because Rust
is where the incident was and where the dispositions are subtle enough to be worth pinning: a
`Drop` that only WAITs is a guard, a bare `kill()` is NOT a finding, and `.output()` reaps by
BLOCKING rather than not at all.
"""

# (label, source, want_findings)
RUST = [
    # ── the incident's shape: a reap that EXISTS, below an assertion that can panic ──────────
    (
        "the incident: reap below a panicking assert",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    assert_eq!(serve(), 200);
    child.kill();
    let _ = child.wait();
}
""",
        True,
    ),
    (
        "the incident again, with `?` as the early return",
        """
use std::process::Command;
#[test]
fn t() -> Result<(), Box<dyn std::error::Error>> {
    let mut child = Command::new("x").spawn()?;
    let port = read()?;
    child.kill();
    let _ = child.wait();
    Ok(())
}
""",
        True,
    ),
    (
        "kill() with no wait(): measured ZOMBIE, and a zombie cannot leak -- NOT a finding",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    child.kill();
    assert!(ok, "the child is already dead when this runs");
}
""",
        False,
    ),
    (
        "kill before the assertion, wait after it: the child is already stopped",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    child.kill();
    assert!(ok, "anything");
    let _ = child.wait();
}
""",
        False,
    ),
    # ── measured rows ───────────────────────────────────────────────────────────────────────
    (
        "a raw Child with no reap at all",
        """
use std::process::Command;
#[test]
fn t() {
    let child = Command::new("x").spawn().expect("runs");
    assert!(child.id() > 0);
}
""",
        True,
    ),
    (
        "kill() with no wait(): measured ZOMBIE, and a zombie cannot leak -- NOT a finding",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    child.kill();
    assert!(ok, "the child is already dead when this runs");
}
""",
        False,
    ),
    (
        "kill() then wait(), nothing panicking between",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    child.kill();
    let _ = child.wait();
    assert!(ok);
}
""",
        False,
    ),
    (
        "wait_with_output() with nothing panicking between",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    let out = child.wait_with_output().expect("output");
    assert!(out.status.success());
}
""",
        False,
    ),
    # ── the drop guard, judged by what its Drop DOES ────────────────────────────────────────
    (
        "a ReapOnDrop guard, assertions below the spawn",
        """
use std::process::{Child, Command};
struct ReapOnDrop(Child);
impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
#[test]
fn t() {
    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));
    assert!(child.0.id() > 0, "panics; the guard reaps on unwind");
}
""",
        False,
    ),
    (
        "a guard built from the handle on the NEXT line",
        """
use std::process::{Child, Command};
struct ReapOnDrop(Child);
impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
#[test]
fn t() {
    let child = Command::new("x").spawn().expect("runs");
    let mut server = ReapOnDrop(child);
    assert!(server.0.id() > 0);
}
""",
        False,
    ),
    (
        "a Drop impl that only logs is NOT a guard",
        """
use std::process::{Child, Command};
struct ReapOnDrop(Child);
impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        eprintln!("dropping");
    }
}
#[test]
fn t() {
    let mut child = ReapOnDrop(Command::new("x").spawn().expect("runs"));
    assert!(child.0.id() > 0);
}
""",
        True,
    ),
    (
        "assert_cmd::Command ships the same contract",
        """
#[test]
fn t() {
    let mut cmd = assert_cmd::Command::cargo_bin("x").unwrap();
    cmd.arg("--bind").arg("127.0.0.1:0");
    let mut child = cmd.spawn().expect("runs");
    assert!(child.wait().is_ok());
}
""",
        False,
    ),
    # ── measured CLEAN rows that a careless checker would flag ───────────────────────────────
    (
        "output() reaps (it blocks instead — measured, and not this gate's business)",
        """
use std::process::Command;
#[test]
fn t() {
    let out = Command::new("x").output().expect("runs");
    assert!(out.status.success());
}
""",
        False,
    ),
    (
        "status() likewise",
        """
use std::process::Command;
#[test]
fn t() {
    let st = Command::new("x").status().expect("runs");
    assert!(st.success());
}
""",
        False,
    ),
    # ── handoff: reported, never a finding ─────────────────────────────────────────────────
    (
        "a spawn RETURNED to the caller is a handoff",
        """
use std::process::{Child, Command};
fn serve() -> Child {
    Command::new("x").spawn().expect("runs")
}
#[test]
fn t() {
    let mut child = serve();
    child.kill();
    let _ = child.wait();
}
""",
        False,
    ),
    # ── not process spawns at all ───────────────────────────────────────────────────────────
    (
        "thread::spawn / tokio::spawn / spawn_blocking are not process spawns",
        """
#[test]
fn t() {
    let h = std::thread::spawn(|| 1);
    let f = async { tokio::spawn(async {}); };
    assert!(h.join().is_ok() && f.is_ok());
}
""",
        False,
    ),
    # ── prose and fixtures are not code ─────────────────────────────────────────────────────
    (
        "a doc comment, a block comment and a string literal each carrying the whole shape",
        """
// Command::new("x").spawn(); child.kill(); child.wait();
/* Command::new("y").spawn().expect("runs"); */
const S: &str = "Command::new(\\"z\\").spawn(); assert!(false); child.kill(); child.wait();";
/// Command::new("w").spawn();
#[test]
fn t() {
    let m = "Command::new(\\"q\\").spawn(); assert!(false); child.kill(); child.wait();";
    assert!(m.contains("spawn"));
}
""",
        False,
    ),
    (
        "a Drop impl that only WAITs is still a guard (Drop for Holder { stdin.take(); wait() })",
        """
use std::io::Write;
use std::process::{Child, Command};
struct Holder { child: Child }
impl Holder {
    fn open() -> Self {
        let mut child = Command::new("x").spawn().expect("runs");
        child.stdin.as_mut().unwrap().write_all(b"go;\\n").unwrap();
        assert!(child.stdout.is_some(), "panics; the Drop still reaps");
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
        False,
    ),
    (
        "a Child handed to wait_timeout(): reaped by contract, not by this file",
        """
use std::process::{Command, Stdio};
#[test]
fn t() {
    let mut stuck = Command::new("sh")
        .args(["-c", "sleep 30"])
        .stdin(Stdio::null())
        .spawn()
        .unwrap();
    assert!(matches!(
        wait_timeout(&mut stuck, Duration::from_millis(200)),
        Outcome::TimedOut
    ));
}
""",
        False,
    ),
    (
        "a deadline WATCHDOG: bounded, reported, not a finding",
        """
use std::process::Command;
#[test]
fn t() {
    let mut child = Command::new("x").spawn().expect("runs");
    let pid = child.id() as i32;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(30));
        unsafe { libc::kill(pid, libc::SIGKILL) };
    });
    assert!(child.id() > 0, "the watchdog bounds the leak; it does not prevent it");
}
""",
        False,
    ),
    (
        "production code above a #[cfg(test)] mod is OUT of scope (the index_view.rs shape)",
        """
pub fn checkout_index(top: &Path, prefix: &Path) -> Result<(), String> {
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
        False,
    ),
    (
        "the same file, but the UNREAPED spawn is INSIDE the cfg(test) mod",
        """
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
        True,
    ),
]
