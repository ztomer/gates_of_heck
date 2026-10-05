"""The rows each fix ADDED to the measured table, with the finding that found them (2026-10-05).

    checks/_unreaped_spawn_table_regressions.py    # nothing to run; import it

A separate file from `_unreaped_spawn_table.py` for one reason: every row here is a shape this gate
got WRONG, and it was wrong the same way three times over -- it read a file and reported green on a
defect in it. R3 says a fixture cannot disagree with the assumption that produced it, and the
assumption that produced all of these was a fixture written by the same person as the checker. So
each row below is the REAL shape, and every one of them was measured passing BEFORE its fix.

That is the part worth stating: these are not new rules with new fixtures justifying them. Each is a
row that went from `clean` (wrong) to `finding` (right) with no change to the input, which is the
only evidence that distinguishes a fix from a rule change.

  RUST-GUARD   a guard on one variable laundering a second, unguarded one   -> `routines`
  RUST-DROP    a `Drop` that does nothing accepted because `Self(` was      -> `monitor`
  RUST-MASK    an `r#"…"#` literal blanking the rest of the file             -> `routines`
  PY-RETURN    a `return` above the reap, where Rust's `PANICS` counts one  -> ZeroThunder
"""

# (label, source, want_findings)
RUST_REGRESSIONS = [
    # ── RUST-GUARD. The reported shape: a raw owner beside a GUARDED server, with the reap below a
    # local helper whose body is an `assert!`. Clean before, twice over -- once because the guard
    # on `child` laundered `owner`, and once because the panicking line was inside a callee.
    (
        "RUST-GUARD: a raw owner beside a Reap guard, the reap below a panicking helper call",
        """
use std::process::{Child, Command};
struct Reap(Child, std::path::PathBuf);
impl Drop for Reap {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
fn up() -> bool {
    true
}
fn wait_for_socket() {
    assert!(up(), "no server ever answered on the socket");
}
#[test]
fn t() {
    let mut owner = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    let mut child = Reap(Command::new("x").spawn().unwrap(), "s".into());
    wait_for_socket();
    owner.kill().unwrap();
    let _ = owner.wait();
    assert!(child.0.id() > 0);
}
""",
        True,
    ),
    (
        "RUST-GUARD: the same, with the panicking line in this function (no callee to resolve)",
        """
use std::process::{Child, Command};
struct Reap(Child);
impl Drop for Reap {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
#[test]
fn t() {
    let mut owner = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    let mut child = Reap(Command::new("x").spawn().unwrap());
    assert!(child.0.id() > 0, "panics, and owner is still a five-minute sleep");
    owner.kill().unwrap();
    let _ = owner.wait();
}
""",
        True,
    ),
    (
        "RUST-GUARD: an EXTERNAL_REAPER call aimed at a DIFFERENT child is not this one's reaper",
        """
use std::process::Command;
use std::time::Duration;
enum Outcome {
    TimedOut,
}
fn wait_timeout(_c: &mut std::process::Child, _d: Duration) -> Outcome {
    Outcome::TimedOut
}
#[test]
fn t() {
    let mut owner = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    let mut other = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    assert!(matches!(wait_timeout(&mut other, Duration::from_millis(200)), Outcome::TimedOut));
    owner.kill().unwrap();
    let _ = owner.wait();
}
""",
        True,
    ),
    (
        "RUST-GUARD: a watchdog armed for a DIFFERENT child does not bound this one",
        """
use std::process::Command;
use std::time::Duration;
fn kill(_pid: i32) {}
#[test]
fn t() {
    let mut owner = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    let mut other = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    let other_pid = other.id() as i32;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(30));
        kill(other_pid);
    });
    assert!(owner.id() > 0);
}
""",
        True,
    ),
    # ── RUST-DROP. `Self(` used to be accepted whenever ANY `impl Drop` existed in the file.
    (
        "RUST-DROP: `Self(...)` into a type whose Drop is EMPTY, with a real guard elsewhere",
        """
use std::process::{Child, Command};
struct Reap(Child);
impl Drop for Reap {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
struct Holder(Child);
impl Drop for Holder {
    fn drop(&mut self) {
    }
}
impl Holder {
    fn spawn(command: &mut Command) -> Self {
        Self(command.spawn().unwrap())
    }
}
#[test]
fn t() {
    let holder = Holder::spawn(Command::new("sh").arg("-c").arg("exec sleep 30"));
    panic!("the failing write_all lands here");
}
""",
        True,
    ),
    (
        "RUST-DROP: `Self { child }` into a type that has NO Drop at all is still a handoff",
        """
use std::process::{Child, Command};
struct Reap(Child);
impl Drop for Reap {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
struct Bare {
    child: Child,
}
impl Bare {
    fn open() -> Self {
        let child = Command::new("x").spawn().unwrap();
        Self { child }
    }
}
#[test]
fn t() {
    let mut bare = Bare::open();
    bare.child.kill().unwrap();
    let _ = bare.child.wait();
}
""",
        False,
    ),
    # ── RUST-MASK. Each of these blanked the whole rest of the file, so a real leak below read clean.
    (
        'RUST-MASK: an r#"…"# raw string above the spawn (the nine sites in routines)',
        """
use std::process::Command;
fn f() { let q = r#"{"op":"shutdown"}"#; }
#[test]
fn t() {
    let mut c = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    assert!(c.id() > 0);
}
""",
        True,
    ),
    (
        'RUST-MASK: a two-hash r##"…"## above the spawn',
        """
use std::process::Command;
fn f() { let q = r##"a"#b"##; }
#[test]
fn t() {
    let mut c = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    assert!(c.id() > 0);
}
""",
        True,
    ),
    (
        'RUST-MASK: a byte-raw br#"…"# above the spawn',
        """
use std::process::Command;
fn f() { let q = br#"AB"#; }
#[test]
fn t() {
    let mut c = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    assert!(c.id() > 0);
}
""",
        True,
    ),
    (
        'RUST-MASK: a raw string ending in a backslash, r"a\\\\", above the spawn',
        """
use std::process::Command;
fn f() { let q = r"a\\\\"; }
#[test]
fn t() {
    let mut c = Command::new("/bin/sleep").arg("300").spawn().unwrap();
    assert!(c.id() > 0);
}
""",
        True,
    ),
    # ── the fixes must not over-reach: these four were FALSE POSITIVES the first attempt produced,
    # and each is here so the over-reach cannot come back unnoticed.
    (
        "RUST-GUARD: the guard is built from the handle MANY lines below, comment in between",
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
    // Nine orphans from this file once accumulated, each holding the cargo build lock, and the
    // only observable was a `cargo test` printing nothing at all. Measured 2026-10-03: NINE
    // orphans from N runs, still holding the cargo build lock, which is why `cargo test` appeared
    // to hang for 30 minutes while the test itself finished in 0.01s. The leak was the finding; the
    // hang was only its symptom. Guard rather than an explicit kill at the end so a panic mid-test
    // cannot leak either.
    let mut child = ReapOnDrop(child);
    assert!(child.0.id() > 0);
}
""",
        False,
    ),
    (
        "RUST-GUARD: `Server { child, .. }` -- a multi-line struct literal, not a call",
        """
use std::process::{Child, Command};
struct Server {
    child: Child,
    stdin: Option<std::process::ChildStdin>,
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stdin = None;
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
fn boot() -> (Server,) {
    let mut child = Command::new("x").spawn().unwrap();
    let stdin = child.stdin.take();
    (Server {
        child,
        stdin,
    },)
}
#[test]
fn t() {
    let (_s,) = boot();
}
""",
        False,
    ),
    (
        "RUST-DROP: the guard is declared INSIDE the test, so `fn drop` sits above the spawn",
        """
use std::process::Command;
#[test]
fn t() {
    struct Kill(std::process::Child);
    impl Drop for Kill {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    let mut child = Command::new("x").spawn().unwrap();
    let _guard = Kill(child_handle(&mut child));
    assert!(true);
}
fn child_handle(c: &mut std::process::Child) -> std::process::Child {
    std::mem::replace(c, Command::new("true").spawn().expect("placeholder"))
}
""",
        False,
    ),
    (
        "RUST-DROP: a guard carrying a LIFETIME is still a guard (the old regex required a `{`)",
        """
use std::process::{Child, Command};
struct ReapOnDrop<'a>(&'a mut Child);
impl Drop for ReapOnDrop<'_> {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
#[test]
fn t() {
    let mut child = Command::new("x").spawn().unwrap();
    let pid = child.id();
    let _guard = ReapOnDrop(&mut child);
    assert!(pid > 0);
}
""",
        False,
    ),
]

PYTHON_REGRESSIONS = [
    (
        "PY-RETURN: a `return` on the readiness path skips the reap (garden_drag_flicker.py:73)",
        """
import subprocess
def run():
    proc = subprocess.Popen(["x"])
    if not ready():
        print("socket down")
        return 2
    proc.terminate()
    return 0
""",
        True,
    ),
    (
        "PY-RETURN: `return proc` hands the handle onward, and is NOT a skip (live_probe_lib.py:31)",
        """
import subprocess
def _launch():
    proc = subprocess.Popen(["x"])
    if ready():
        return proc
    proc.terminate()
    proc.wait(timeout=5)
    return None
""",
        False,
    ),
    (
        "PY-RETURN: `return` under `poll()` -- the child is already gone, so there is nothing to reap",
        """
import subprocess
def boot():
    proc = subprocess.Popen(["x"])
    while time.time() < deadline:
        if proc.poll() is not None:
            target_pid(None)
            return None
        if ping().get("ok"):
            return proc
        time.sleep(0.5)
    proc.terminate()
    return None
""",
        False,
    ),
    (
        "PY-RETURN: a `try` ABOVE the spawn with the reap in its `finally` (real_app_e2e.py:82)",
        """
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
        False,
    ),
]

# NOT a regression row, and kept OUT of the list above on purpose.
#
# A backslash DOES escape the closing quote in a Python RAW literal, and this was measured rather
# than reasoned about because the first attempt got it backwards: `r"a\""` is ONE string whose
# content is `a\"` (CPython 3.14, `exec`'d and printed), while rustc says `r"a\"b"` will not
# compile at all. So a masker that branches on `r` and stops treating `\` as an escape -- the
# "obvious" fix for the Rust `r#"…"#` desync -- blanks four real literals in the estate's own shape.
# The branch was written, measured, and removed; this row is here so the next person does not
# re-derive it.
#
# `want_findings=True` is the load-bearing part: a Popen with no reap below a raw literal is a
# FINDING, so this row goes RED only while the scanner is still in sync. Branch on `r` and it turns
# clean, which is the desync arriving through the door that looks like a fix.
PYTHON_RAW_QUOTE_CALIBRATION = (
    "a raw literal ending in an escaped quote is ONE string (CPython escapes it; rustc will not even "
    "compile it), so the `\\` must arm the escape state — a finding here PROVES the scanner is in "
    "sync",
    'import subprocess\nX = r"a\\""\ndef test_x():\n    proc = subprocess.Popen(["x"])\n'
    "    assert proc is not None\n",
    True,
)
