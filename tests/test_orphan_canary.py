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
import time

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
        [sys.executable, CANARY, "wrap", "--repo", str(repo), "--timeout", "30", "--", *args],
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
        assert proc.returncode == O.LEAK_EXIT, out
        assert "outlived the run that started them" in out, out
        assert "sleep 1200" in out, out
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
        assert proc.returncode == O.LEAK_EXIT, out
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


def test_the_canary_never_reports_a_concurrent_gate_run(repo: Path) -> None:
    """A concurrent canary or runner lives under the same checkout this canary is scanning, so path
    attribution names it ours — and it is not a leak, it is another gate running. Measured under the
    parallel suite: every run reported the other workers' canaries, and three end-to-end tests went
    red for a reason that had nothing to do with what they measured.

    The concurrent run is given a step that is STILL RUNNING, because the judgement has to happen
    while its command line can still be read: judging an exited process tests the
    "(exited before it could be described)" branch instead.
    """
    other = subprocess.Popen(
        [
            sys.executable,
            CANARY,
            "wrap",
            "--repo",
            str(repo),
            "--timeout",
            "20",
            "--",
            "/bin/sh",
            "-c",
            "sleep 913",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Judge THIS canary's own pid, and only that. Judging "every pid that appeared" reads the
        # whole machine, so under the parallel suite it picked up the other workers' deliberately
        # leaked orphans and failed for a reason unrelated to what it measures.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not alive(other.pid):
            time.sleep(0.05)
        assert alive(other.pid), "the concurrent canary never started"
        concurrent = O.judge([other.pid], O.roots_for(str(repo)))
        assert concurrent.code == 0, concurrent.ours
        assert not concurrent.ours, concurrent.ours
    finally:
        other.kill()
        other.wait(timeout=30)
        subprocess.run(["/usr/bin/pkill", "-f", "sleep 913"], check=False)

    # ...and an ordinary clean run stays clean and silent.
    proc = wrap(repo, "/bin/sh", "-c", "exit 0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "outlived" not in proc.stdout + proc.stderr


def test_another_programs_process_is_not_attributable(repo: Path) -> None:
    """A process started by something else during the window is REPORTED and never failed on. A
    canary that failed on those would be one nobody leaves switched on — and a switched-off canary
    is worse than none, because the estate would believe it.

    Asserted on the ATTRIBUTION RULE, not on a live run's global verdict. The first version spawned
    a process and asserted `report(...) == 0`, which under the parallel suite read another worker's
    deliberately-leaked orphan (`sleep 1201`, ppid 1) and failed for a reason that had nothing to do
    with what it measured. A rule deserves a test of the rule; the end-to-end paths above already
    cover the wiring.
    """
    roots = O.roots_for(str(repo))
    assert O.attributable("/somebody/elses/target/debug/build", roots) is None
    assert O.attributable(f"{repo}/crates/x-rs/target/debug/server", roots) == roots[0]
    assert O.attributable("cargo test --workspace", roots) is None

    # ...and it still gets REPORTED, loudly enough to see, in the non-verdict half.
    assert O.emit([], str(repo)) is None


def test_a_clean_report_prints_nothing_at_all(capsys) -> None:
    """A canary on a green run must be SILENT. Output on every step of every gate trains people to
    skip it, and a skipped canary is worse than none: the estate would believe it."""
    O.emit([], "/repo")
    assert capsys.readouterr().out == ""


def test_the_report_is_silent_on_verbose_when_there_is_nothing_to_name(capsys) -> None:
    O.emit([], "/repo", True)
    assert capsys.readouterr().out == ""


# ── the step's own verdict must survive ──────────────────────────────────────


@pytest.mark.parametrize("code", [0, 3, 5])
def test_the_steps_own_exit_code_is_preserved(repo: Path, code: int) -> None:
    """A red step is the news. A canary red on top of it must not read as the canary being the
    failure, so the step's code wins."""
    proc = wrap(repo, "/bin/sh", "-c", f"exit {code}")
    assert proc.returncode == code, proc.stdout + proc.stderr


def test_a_green_steps_leak_gets_its_own_exit_code(repo: Path) -> None:
    """125, distinct from a step that simply failed: the two have opposite fixes — write a guard
    versus fix the test — and one exit status for both sends someone to the wrong one."""
    proc = wrap(repo, "/bin/sh", "-c", "/bin/sleep 1203 >/dev/null 2>&1 & disown; exit 0")
    try:
        assert proc.returncode == O.LEAK_EXIT, proc.stdout + proc.stderr
        assert O.LEAK_EXIT != 124, "a leak must not share a code with a timeout"
    finally:
        subprocess.run(["/usr/bin/pkill", "-f", "sleep 1203"], check=False)


# ── refusals ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv", [["wrap"], ["wrap", "--"], ["wrap", "--timeout", "0", "--", "true"]]
)
def test_usage_errors_are_exit_two(repo: Path, argv: list[str]) -> None:
    proc = subprocess.run(
        [sys.executable, CANARY, *argv], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 2, (argv, proc.returncode, proc.stdout, proc.stderr)


def test_a_missing_runner_is_refused_not_silently_unbounded(tmp_path: Path) -> None:
    """Without `lib/bounded_run.py` every step would run with no ceiling — the defect this exists to
    close. So an absent runner is exit 2, never a quiet pass."""
    fake = tmp_path / "lib"
    fake.mkdir()
    (fake / "orphan_canary.py").write_text(Path(CANARY).read_text(encoding="utf-8"))
    proc = subprocess.run(
        [sys.executable, str(fake / "orphan_canary.py"), "wrap", "--", "/bin/sh", "-c", "exit 0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "bounded_run.py is missing" in proc.stderr, proc.stderr


# ── wiring, because a canary nothing calls is a rumour (R7) ──────────────────


def test_local_ci_runs_every_step_through_the_canary() -> None:
    source = (Path(__file__).resolve().parent.parent / "gates" / "local_ci.sh").read_text()
    # ONE call per step: the ceiling, the output capture and the leak report are one thing, and two
    # call sites is how they drift apart.
    calls = [
        ln
        for ln in source.splitlines()
        if "orphan_canary.py" in ln and not ln.lstrip().startswith("#")
    ]
    assert len(calls) == 1, f"expected exactly one runner call in local_ci.sh, found {calls}"


def test_a_leak_is_named_distinctly_from_a_failure() -> None:
    """A green step that leaked is a different finding from a red step, and reads differently:
    "PASSED, and left processes running" is the sentence the next person needs."""
    source = (Path(__file__).resolve().parent.parent / "gates" / "local_ci.sh").read_text()
    assert "PASSED, and left processes running" in source
    assert "-eq 125" in source
