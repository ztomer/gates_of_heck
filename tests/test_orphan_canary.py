"""`lib/orphan_canary.py` — the report that makes the NEXT occurrence loud instead of silent.

media_server, 2026-10-03: a leaked server outlived the test that started it, held the cargo build
lock, and every later `cargo test` blocked with no output at all. A ceiling cannot catch this: the
step exits 0. So the canary measures the process table either side of every step.

THE CALIBRATION THAT MATTERS, and it is in this file's history twice over. The first version of
`wrap` handed `report()` the AFTER table as its `before` argument, so every orphan appeared in both
tables, `new` was empty, and the canary reported a clean run while `sleep 408` was still alive with
ppid 1. It also excluded only its own DESCENDANTS, so `gates/local_ci.sh` — which runs the canary as
its own process — had every passing step failed, attributed to the canary watching it.

A canary's only product is being right about the case it exists for, so both of those are tests
here rather than notes.
"""

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import orphan_canary as O  # noqa: E402

CANARY = str(Path(O.__file__))


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo-shaped directory, so attribution has a root to name. `target/debug` exists because
    that is where a leaked cargo-built binary lands -- the incident's exact path."""
    root = tmp_path / "proj"
    (root / "crates" / "x-rs" / "target" / "debug").mkdir(parents=True)
    (root / "src").mkdir()
    return root


def wrap(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, CANARY, "wrap", str(repo / "snap.json"), "--repo", str(repo), "--", *args],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
    )


# ── the incident's shape: a step that PASSES and leaks a server ──────────────


def test_a_step_that_leaks_a_server_is_red(repo: Path) -> None:
    """THE case. The step exits 0 — the suite is green — and the server is still running. This is
    what `lib/bounded_run.py` cannot see, because nothing hung."""
    proc = wrap(repo, "/bin/sh", "-c", "/bin/sleep 1200 >/dev/null 2>&1 & disown; exit 0")
    try:
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, out
        assert "outlived the run that started them" in out, out
        assert "sleep 1200" in out, out
        assert "orphaned" in out, "an orphan is named as one: its parent is gone (ppid 1)"
    finally:
        subprocess.run(["/usr/bin/pkill", "-f", "sleep 1200"], check=False)


def test_a_leaked_binary_under_the_repos_target_dir_is_red(repo: Path) -> None:
    """Attribution by PATH, which is the other half and the one the incident needed: the leaked
    process was `…/target/debug/archive_torznab`, naming nothing about being an orphan at first
    glance."""
    fake = repo / "crates" / "x-rs" / "target" / "debug" / "archive_torznab"
    # Re-execs ITSELF in the background, so the surviving process's own argv is the repo's target
    # path. That is the attribution arm under test: a plain backgrounded `sleep` would be caught by
    # the ppid-1 arm instead and this test would prove nothing about paths.
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--daemon" ]; then /bin/sleep 1201; exit 0; fi\n'
        '"$0" --daemon &\n'
        "disown\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    proc = wrap(repo, "/bin/sh", "-c", str(fake))
    try:
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, out
        assert "archive_torznab" in out, out
        assert "orphaned" not in out.split("outlived")[1][:200], (
            "attribution must come from the PATH here, not from ppid 1 — otherwise this test is "
            "the ppid arm wearing the path arm's name"
        )
    finally:
        subprocess.run(["/usr/bin/pkill", "-f", "sleep 1201"], check=False)


# ── it must not cry wolf, or it gets switched off ────────────────────────────


def test_a_step_that_leaves_nothing_is_green_and_says_nothing_about_orphans(repo: Path) -> None:
    proc = wrap(repo, "/bin/sh", "-c", "exit 0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "outlived" not in proc.stdout + proc.stderr


def test_the_canary_never_reports_itself(repo: Path) -> None:
    """`gates/local_ci.sh` runs the canary as its own process, so its command line names a path
    inside the repo being scanned. Measured: `[2/2] true` — a passing step — failed because of
    `…/gates_of_heck/lib/orphan_canary.py`."""
    proc = wrap(repo, "/bin/sh", "-c", "exit 0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Not "the string is absent" — the canary's own OUTPUT names itself. What must be absent is the
    # canary's PROCESS appearing as a reported process line.
    reported = [ln for ln in proc.stdout.splitlines() if ln.startswith("    ") and "pid " in ln]
    assert not any("orphan_canary.py" in ln for ln in reported), reported


def test_a_step_that_leaks_nothing_but_another_programs_process_started_is_not_failed(
    repo: Path,
) -> None:
    """A process started by something else during the window is REPORTED and never failed on. A
    canary that failed on those would be one nobody leaves switched on — and a switched-off canary
    is worse than none, because the estate would believe it."""
    before = O.snapshot()
    subprocess.Popen(
        ["/bin/sh", "-c", "sleep 0.4"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    verdict, ours, theirs = O.report(before, O.roots_for(str(repo)), str(repo))
    assert verdict == 0, (ours, theirs)
    assert not ours, ours


def test_the_unattributable_half_is_one_line_not_a_list(repo: Path) -> None:
    """Measured through `local_ci.sh`: three of another repo's `sccache`/`rustc` plus a system
    daemon, on a GREEN run of two trivial steps. Per-process output would be twenty lines of other
    people's work after every step of every gate."""
    proc = wrap(repo, "/bin/sh", "-c", "exit 0")
    out = proc.stdout + proc.stderr
    assert "started elsewhere on this machine" not in out or out.count("pid ") == 0, out


# ── the step's own verdict must survive ──────────────────────────────────────


@pytest.mark.parametrize("code", [0, 3, 5])
def test_the_steps_own_exit_code_is_preserved(repo: Path, code: int) -> None:
    """A red step is the news. A canary red on top of it must not read as the canary being the
    failure, so the step's code wins and the canary's verdict is the fallback."""
    proc = wrap(repo, "/bin/sh", "-c", f"exit {code}")
    assert proc.returncode == code, proc.stdout + proc.stderr


# ── take / since, and the refusals ───────────────────────────────────────────


def test_take_then_since_names_a_leak_across_two_invocations(repo: Path) -> None:
    """The two-mode form is what a gate wraps around a command it does not own."""
    snap = repo / "before.json"
    taken = subprocess.run(
        [sys.executable, CANARY, "take", str(snap)], capture_output=True, text=True, check=False
    )
    assert taken.returncode == 0, taken.stderr
    assert json.loads(snap.read_text())["v"] == O.SNAPSHOT_VERSION
    subprocess.Popen(["/bin/sh", "-c", "/bin/sleep 1202 & disown; exit 0"])
    try:
        proc = subprocess.run(
            [sys.executable, CANARY, "since", str(snap), "--repo", str(repo)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "sleep 1202" in proc.stdout + proc.stderr
    finally:
        subprocess.run(["/usr/bin/pkill", "-f", "sleep 1202"], check=False)


def test_usage_errors_are_exit_two(repo: Path) -> None:
    for argv in (
        ["take"],
        ["since"],
        ["wrap", str(repo / "s.json"), "--"],
    ):
        proc = subprocess.run(
            [sys.executable, CANARY, *argv], capture_output=True, text=True, check=False
        )
        assert proc.returncode == 2, (argv, proc.returncode, proc.stdout, proc.stderr)


def test_an_unreadable_snapshot_is_exit_two_not_a_silent_pass(repo: Path) -> None:
    """A canary that could not read its own baseline must not report "nothing survived" — that is a
    pass over nothing, which `checks/check_empty_scope.py` exists to catch in every gate."""
    proc = subprocess.run(
        [sys.executable, CANARY, "since", str(repo / "nope.json"), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "cannot read" in proc.stderr, proc.stderr


# ── wiring, because a canary nothing calls is a rumour (R7) ──────────────────


def test_local_ci_brackets_every_step_with_the_canary() -> None:
    source = (Path(__file__).resolve().parent.parent / "gates" / "local_ci.sh").read_text()
    assert 'orphan_canary.py" take' in source, "no before-snapshot: the diff has nothing to diff"
    assert 'orphan_canary.py" since' in source
    # ...and a leak FAILS the run, not merely prints. A canary that reports and returns 0 is
    # decoration.
    assert "orphan_rc" in source and "FAILED=$((FAILED + 1))" in source


def test_a_leak_is_named_distinctly_from_a_failure() -> None:
    """A green step that leaked is a different finding from a red step, and reads differently:
    "PASSED, and left processes running" is the sentence the next person needs."""
    source = (Path(__file__).resolve().parent.parent / "gates" / "local_ci.sh").read_text()
    assert "PASSED, and left processes running" in source
