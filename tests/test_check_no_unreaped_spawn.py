"""`check_no_unreaped_spawn.py` — the gate that would have caught the 2026-10-03 leak.

THE INCIDENT, in one sentence: `media_server/crates/archive-torznab-rs/tests/test_net_and_bin.rs`
spawned the real binary (`--bind 127.0.0.1:0`, a server that loops forever by design) and reaped it
with an explicit `child.kill(); child.wait();` placed AFTER the assertions — so every assertion
failure below that point leaked a live server, nine of them accumulated, each holding the cargo build
lock, and the only observable was a `cargo test` that printed nothing at all.

These tests assert the checker refuses that shape and accepts every shape the MEASURED table says it
must. The table itself is in the checker's docstring and is re-asserted by its `--probe`; R1 holds
that the probe is the load-bearing half, because a test and its checker share an assumption and
agree by construction.
"""

from pathlib import Path
import subprocess
import sys

import pytest

from conftest import REPO_ROOT, commit_all, git, run_check, write

CHECK = "checks/check_no_unreaped_spawn.py"


def findings(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return run_check(repo, CHECK, *args)


# ── the incident, in the shape it actually had ───────────────────────────────
# QUOTED from media_server at 052772a, the last commit before cc70f6a fixed it. Not a paraphrase:
# an earlier draft of this fixture dropped the `.expect()` calls and the `unwrap_or_else(panic)`
# between the spawn and the pair, and the checker reported it CLEAN -- which is the R3 failure
# reproduced inside the gate that exists to catch it. The lines that panic are the ones that matter,
# and they are the lines a fixture writer removes because they look like plumbing.
INCIDENT = """\
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::process::{Command, Stdio};
use std::time::Duration;

#[test]
fn a_request_the_server_answers_is_served_by_the_real_binary() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_archive_torznab"))
        .args(["--bind", "127.0.0.1:0"])
        .stderr(Stdio::piped())
        .spawn()
        .expect("the binary runs");
    let mut stderr = BufReader::new(child.stderr.take().expect("piped stderr"));
    let mut banner = String::new();
    stderr
        .read_line(&mut banner)
        .expect("the binary announces its address");
    let port = banner
        .rsplit_once(':')
        .and_then(|(_, p) p.trim().parse::<u16>().ok())
        .unwrap_or_else(|| panic!("no port in the binary's own address line: {banner:?}"));
    assert_ne!(
        port, 0,
        "the kernel assigns a real port; 0 means we read the request back"
    );

    let mut raw = String::new();
    let mut served = false;
    for _ in 0..100 {
        match TcpStream::connect(("127.0.0.1", port)) {
            Err(_) => std::thread::sleep(Duration::from_millis(50)),
            Ok(mut s) => {
                let _ = s.set_read_timeout(Some(Duration::from_secs(20)));
                let _ = write!(s, "GET /?t=caps HTTP/1.1\\r\\nHost: x\\r\\n\\r\\n");
                let mut attempt = String::new();
                if s.read_to_string(&mut attempt).is_ok() {
                    raw = attempt;
                    served = raw.contains("200 OK") && raw.contains("<caps");
                }
                if served {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();

    assert!(
        served,
        "the binary never served caps on {port}; last response: {raw}"
    );
    assert!(raw.contains("200 OK"), "{raw}");
}
"""


def test_the_incident_shape_is_red(repo: Path) -> None:
    write(repo, "crates/x-rs/tests/test_net_and_bin.rs", INCIDENT)
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"the incident passed this gate:\n{got.stdout}\n{got.stderr}"
    out = got.stdout + got.stderr
    # Named as file:line, and at the SPAWN rather than at the reap -- the spawn is where the child
    # becomes unreachable-on-panic, and a finding pointing at the reap reads as "the kill is wrong".
    assert "crates/x-rs/tests/test_net_and_bin.rs:11" in out, out
    # Named as the ORDERING defect. A reap DOES exist in this file, so reporting it as a missing
    # reap would be reporting the wrong thing -- and would be satisfied by deleting the kill.
    assert "reap-after-panic" in out, out
    # ...and it must name the panic it found, because "there is a panic somewhere above" is the
    # difference between a finding and a hint.
    assert "AFTER a construct that can panic at line" in out, out


def test_each_panicking_shape_in_the_incident_is_its_own_trigger(repo: Path) -> None:
    """The real file panics FOUR ways between the spawn and the pair. Removing any one of them must
    not silence the gate, because the remaining three are still there."""
    triggers = [
        # Each entry REMOVES one trigger (the second element) by replacing the first with a line
        # that cannot panic. All four are the real file's, and the four removals together leave a
        # file that is genuinely clean -- which is the other half: a gate that cannot report the
        # fix is not measuring the defect.
        (
            'child.stderr.take().expect("piped stderr")',
            '.expect("the binary announces its address")',
        ),
        (
            '.expect("the binary announces its address")',
            'unwrap_or_else(|| panic!("no port in the binary\'s own address line: {banner:?}"))',
        ),
        (
            'unwrap_or_else(|| panic!("no port in the binary\'s own address line: {banner:?}"))',
            "assert_ne!(",
        ),
        ("assert_ne!(", "let mut raw = String::new();"),
    ]
    for kept, removed in triggers:
        body = INCIDENT.replace(kept, removed)
        assert body != INCIDENT, f"the trigger {kept!r} is not in the incident fixture"
        write(repo, "crates/x-rs/tests/test_net_and_bin.rs", body)
        commit_all(repo)
        got = findings(repo)
        assert got.returncode == 1, (
            f"removing {removed!r} silenced the gate; {kept!r} was carrying it alone\n{got.stdout}"
        )
    # ...and with EVERY trigger gone, the file is genuinely clean: the reap now precedes every
    # panicking construct, so a finding would be wrong. The gate has to be able to say so, or it is
    # not measuring anything.
    body = INCIDENT
    for kept, _ in triggers:
        body = body.replace(kept, "let _ = ();")
    write(repo, "crates/x-rs/tests/test_net_and_bin.rs", body)
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"the checker cannot see the fix it was written for:\n{got.stdout}"


def test_the_incident_shape_is_red_after_a_repair_that_only_renames(repo: Path) -> None:
    """A rename-only fix is not a fix, and must not be one here either.

    The second defect in that same file was `serve()` printing the address it was ASKED for rather
    than `listener.local_addr()`; a rename had already been committed for it. This pins the
    orthogonal half: renaming the variable holding the reap does not move the reap, and a gate that
    matched on names would call that repaired.
    """
    write(
        repo,
        "crates/x-rs/tests/test_net_and_bin.rs",
        INCIDENT.replace("child.kill();", "server.kill();").replace(
            "child.wait();", "server.wait();"
        ),
    )
    commit_all(repo)
    assert findings(repo).returncode == 1


def test_a_guard_is_clean_even_with_assertions_below_the_spawn(repo: Path) -> None:
    """The shape the fix actually shipped: a Drop impl, so the reap runs on unwind too."""
    write(
        repo,
        "crates/x-rs/tests/test_bin.rs",
        """\
use std::process::{Child, Command, Stdio};

struct ReapOnDrop(Child);

impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn the_binary_serves() {
    let mut child = ReapOnDrop(
        Command::new(env!("CARGO_BIN_EXE_x"))
            .args(["--bind", "127.0.0.1:0"])
            .stderr(Stdio::piped())
            .spawn()
            .expect("runs"),
    );
    let port = read_port(&mut child);
    assert!(port != 0, "the kernel assigns a real port");
    let _ = child.0.status();
}

fn read_port(_c: &mut ReapOnDrop) -> u16 {
    0
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"a kill-on-drop guard was refused:\n{got.stdout}\n{got.stderr}"


def test_kill_then_wait_before_the_first_assertion_is_clean(repo: Path) -> None:
    """The one explicit-reap shape that is genuinely safe, kept so the rule cannot be read as
    'any explicit reap is a finding'."""
    write(
        repo,
        "crates/x-rs/tests/test_bin.rs",
        """\
use std::process::Command;

#[test]
fn it_runs_once() {
    let mut child = Command::new("x").spawn().expect("runs");
    let out = child.wait_with_output().expect("output");
    assert!(out.status.success());
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"


def test_the_probe_goes_red_when_the_ordering_rule_is_dropped(repo: Path) -> None:
    """R3, self-inflicted and measured: delete the ordering rule and watch the probe refuse.

    A gate whose rule list was narrowed to nothing would still exit 0 on every real tree, and the
    registry would go on calling it proven. The probe is what notices.
    """
    import importlib

    sys.path.insert(0, str(REPO_ROOT / "checks"))
    try:
        module = importlib.import_module("check_no_unreaped_spawn")
        importlib.reload(module)
        shapes = importlib.import_module("_spawn_shapes")
        importlib.reload(shapes)
        proof = importlib.import_module("_unreaped_spawn_probe")
        importlib.reload(proof)
        # The rule the gate exists for, switched off at its own named seam -- in the module that
        # OWNS it, which after the 500-line split is not the module the probe imports from. Not a
        # text edit to a copy: a mutated file proves the COPY refuses, and the thing that has to
        # notice is the probe the sweep runs in 30 repos.
        assert proof.probe() == 0, "the probe is not green to begin with; fix that first"
        saved = shapes._ordering_finding
        shapes._ordering_finding = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        try:
            assert proof.probe() != 0, (
                "the probe passed with the ordering rule removed — a gate narrowed to nothing "
                "would still exit 0 over every real tree"
            )
        finally:
            shapes._ordering_finding = saved
        assert proof.probe() == 0, "the probe did not come back green when the rule was restored"
    finally:
        sys.path.pop(0)


# ── Rust: the dispositions the MEASURED table records ────────────────────────


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (
            'let c = Command::new("x").spawn().expect("runs");\n',
            "a spawn whose Child is dropped and never killed: measured LIVE ORPHAN",
        ),
        (
            'let mut c = Command::new("x").spawn().expect("runs");\nassert!(ok);\n'
            "c.kill();\nlet _ = c.wait();\n",
            "the incident's ordering, in miniature",
        ),
        (
            'let mut c = Command::new("x").spawn().expect("runs");\n'
            'let p = read(&mut c).unwrap_or_else(|| panic!("no port"));\n'
            'assert_ne!(p, 0, "a real port");\nc.kill();\nlet _ = c.wait();\n',
            "the incident's real triggers: an unwrap_or_else(panic) AND an assert above the pair",
        ),
        (
            'let mut c = Command::new("x").spawn().expect("runs");\n'
            "let p = read(&mut c)?;\nc.kill();\nlet _ = c.wait();\n",
            "an early `?` above the pair skips the reap exactly as a panic does",
        ),
    ],
)
def test_rust_findings(repo: Path, body: str, why: str) -> None:
    write(
        repo,
        "crates/x-rs/tests/test_a.rs",
        "use std::process::Command;\n\n#[test]\nfn t() {\n    "
        + body.replace("\n", "\n    ")
        + "\n}\n",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"expected a finding ({why}):\n{got.stdout}\n{got.stderr}"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ('let out = Command::new("x").output().expect("runs");\n', "output() reaps (measured)"),
        ('let st = Command::new("x").status().expect("runs");\n', "status() reaps (measured)"),
        (
            'let mut c = Command::new("x").spawn().expect("runs");\n'
            "let _ = c.kill();\nlet _ = c.wait();\n",
            "explicit kill+wait with nothing panicking between",
        ),
        (
            'let mut c = Command::new("x").spawn().expect("runs");\n'
            'c.kill();\nassert!(ok, "the child is already dead here");\n',
            "kill() with no wait() is NOT this gate: a zombie cannot hold a lock or outlive the run",
        ),
    ],
)
def test_rust_clean(repo: Path, body: str, why: str) -> None:
    write(
        repo,
        "crates/x-rs/tests/test_a.rs",
        "use std::process::Command;\n\n#[test]\nfn t() {\n    "
        + body.replace("\n", "\n    ")
        + "\n}\n",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"false positive ({why}):\n{got.stdout}\n{got.stderr}"


def test_output_does_not_hide_a_real_find(repo: Path) -> None:
    """`.output()` reaps but BLOCKS forever on a long-running child — measured, still running at
    5 s on a `sleep 300`. So it is not a finding for THIS gate, and a test file may use it freely;
    the unbounded wait is item three's subject and lives in the gate runner."""
    write(
        repo,
        "crates/x-rs/tests/test_a.rs",
        "use std::process::Command;\n\n#[test]\nfn t() {\n"
        '    let out = Command::new("x").output().expect("runs");\n'
        "    assert!(out.status.success());\n}\n",
    )
    commit_all(repo)
    assert findings(repo).returncode == 0


def test_a_spawn_handed_to_the_caller_is_a_handoff_not_a_finding(repo: Path) -> None:
    """Documented blind spot, pinned so it cannot quietly become a finding or quietly vanish."""
    write(
        repo,
        "crates/x-rs/tests/test_a.rs",
        """\
use std::process::{Child, Command};

fn serve() -> Child {
    Command::new("x").spawn().expect("runs")
}

#[test]
fn t() {
    let mut c = serve();
    let _ = c.kill();
    let _ = c.wait();
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"
    assert "1 handoff" in (got.stdout + got.stderr), got.stdout


def test_prose_and_string_literals_are_not_code(repo: Path) -> None:
    """A checker that reads a fixture or a doc comment as code is the R6 failure. Every string here
    carries the full spawn shape and none of it is code."""
    write(
        repo,
        "crates/x-rs/tests/test_a.rs",
        """\
// Command::new("x").spawn(); then child.kill(); child.wait();
// /* Command::new("y").spawn().expect("runs"); */
const S: &str = "Command::new(\\"z\\").spawn().expect(\\"runs\\");";

/// Command::new("w").spawn();
#[test]
fn t() {
    let msg = "Command::new(\\"q\\").spawn(); assert!(false); child.kill(); child.wait();";
    assert!(msg.contains("spawn"));
}
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"


# ── Python ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "want_finding", "why"),
    [
        (
            'def test_x():\n    proc = subprocess.Popen(["x"])\n    assert proc is not None\n',
            True,
            "no `with`, no wait: measured LIVE ORPHAN",
        ),
        (
            'def test_x():\n    with subprocess.Popen(["x"]) as proc:\n'
            "        assert proc is not None\n",
            False,
            "a context manager IS the guard",
        ),
        (
            'def test_x():\n    proc = subprocess.Popen(["x"])\n    assert proc.wait() == 0\n',
            False,
            "an explicit wait, with nothing raising between",
        ),
    ],
)
def test_python_popen_dispositions(repo: Path, body: str, want_finding: bool, why: str) -> None:
    write(repo, "tests/test_server.py", "import subprocess\nimport sys\n\n\n" + body)
    commit_all(repo)
    got = findings(repo)
    assert (got.returncode == 1) is want_finding, f"{why}:\n{got.stdout}\n{got.stderr}"


# ── shell ────────────────────────────────────────────────────────────────────


def test_shell_background_launch_with_no_kill_is_red(repo: Path) -> None:
    write(
        repo,
        "tests/test_server.sh",
        """\
#!/usr/bin/env bash
set -euo pipefail
"$GOH_BIN" --bind 127.0.0.1:0 &
echo started
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 1, f"{got.stdout}\n{got.stderr}"


def test_shell_background_launch_with_a_trap_is_clean(repo: Path) -> None:
    write(
        repo,
        "tests/test_server.sh",
        """\
#!/usr/bin/env bash
set -euo pipefail
server_pid=""
cleanup() { [ -n "$server_pid" ] && kill "$server_pid" 2>/dev/null || true; }
trap cleanup EXIT
"$GOH_BIN" --bind 127.0.0.1:0 &
server_pid=$!
echo started
""",
    )
    commit_all(repo)
    got = findings(repo)
    assert got.returncode == 0, f"{got.stdout}\n{got.stderr}"
