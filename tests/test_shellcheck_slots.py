"""The machine's shellcheck children are bounded, whoever starts them (GOH.MEMADMIT).

THE INCIDENT (2026-11-16). The machine thrashed from ~00:45 to a forced restart at 06:16: a
JetsamEvent counted 128 shellcheck processes holding 63.7 GiB (median 453 MB, max 1167 MB each).
`goh shell-lint` ran one shellcheck per file on one thread per core (16) PER RUN, and about eight
runs overlapped -- land gates, lanes' test runs under xdist, the tests that lint whole trees -- with
nothing counting across them. The bound is now the machine's: a counting semaphore of flock'd token
files (lib/gate_slot.py's mechanism), `GOH_SHELLCHECK_SLOTS` tokens in `GOH_SHELLCHECK_SLOT_DIR`.

And one shellcheck costs less: shellcheck's dataflow analysis (`--extended-analysis`, on by
default since 0.10) was what took one call over goh's own `gates/rust_gate.sh` to 1550 MB and
6.7 s; without it, 201 MB and 2.6 s, and the whole tree 28.3 GB-summed / 144 s down to 5.5 GB /
43 s. It finds only notes (SC2317 unreachable, SC2329 never invoked: 0 differences at warning over
goh's 104 files and the estate's 310), so it is off at `warning` and `error` and on below them.

Pinned behaviourally with a fake shellcheck that records how many calls run at once.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from _tool_versions import floor
from conftest import commit_all, native_goh_path, write
from timing_bounds import patience

SC_FLOOR = floor("shellcheck")
RUNS = 4
FILES = 6
BOUND = 3


def _counting_shellcheck(tmp: Path) -> tuple[Path, Path, Path]:
    """A shellcheck that passes every file, and logs how many calls are alive as it starts."""
    bin_ = tmp / "fake-sc"
    bin_.mkdir(parents=True)
    marks = tmp / "marks"
    marks.mkdir()
    log = tmp / "alive.log"
    argv = tmp / "argv.log"
    sc = bin_ / "shellcheck"
    sc.write_text(
        "#!/bin/bash\n"
        f'[ "${{1:-}}" = --version ] && {{ printf "ShellCheck\\nversion: {SC_FLOOR}\\n"; exit; }}\n'
        f'echo "$*" >> "{argv}"\n'
        f'marks="{marks}"; : > "$marks/$$"\n'
        f'ls "$marks" | wc -l | tr -d " " >> "{log}"\n'
        "sleep 0.4\n"
        'rm -f "$marks/$$"\n'
    )
    sc.chmod(0o755)
    return bin_, log, argv


def _env(tmp: Path, bin_: Path, slots: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "SHELLCHECK_OPTS"}
    env["GOH_VERDICT_CACHE"] = "0"
    env["GOH_SHELLCHECK_SLOTS"] = slots
    env["GOH_SHELLCHECK_SLOT_DIR"] = str(tmp / "sc-slots")
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    return env


def _lint_side_by_side(repo: Path, tmp: Path, slots: str) -> int:
    """RUNS concurrent `goh shell-lint` runs; the most shellcheck calls seen alive at once."""
    for i in range(FILES):
        write(repo, f"s{i}.sh", "#!/usr/bin/env bash\necho hi\n")
    commit_all(repo)
    bin_, log, _ = _counting_shellcheck(tmp)
    env = _env(tmp, bin_, slots)
    procs = [
        subprocess.Popen(
            [str(native_goh_path()), "shell-lint"],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for _ in range(RUNS)
    ]
    for p in procs:
        out, _ = p.communicate(timeout=patience(120))
        assert p.returncode == 0, out
    counts = [int(n) for n in log.read_text().split()]
    assert len(counts) == RUNS * FILES, counts
    return max(counts)


@pytest.mark.load_sensitive
def test_concurrent_lint_runs_never_exceed_the_machine_bound(repo, tmp_path):
    seen = _lint_side_by_side(repo, tmp_path, str(BOUND))
    assert seen <= BOUND, f"{seen} shellcheck calls alive at once under a bound of {BOUND}"


@pytest.mark.load_sensitive
def test_calibration_without_the_bound_the_same_runs_exceed_it(repo, tmp_path):
    """The instrument can read the wrong answer: with the bound off, the count goes over it."""
    seen = _lint_side_by_side(repo, tmp_path, "0")
    assert seen > BOUND, f"only {seen} calls at once with no bound: the test cannot see a breach"


def _argv_at(repo: Path, tmp: Path, gatesrc: str | None) -> str:
    if gatesrc is not None:
        write(repo, ".gatesrc", gatesrc)
    write(repo, "a.sh", "#!/usr/bin/env bash\necho hi\n")
    commit_all(repo)
    bin_, _, argv = _counting_shellcheck(tmp)
    r = subprocess.run(
        [str(native_goh_path()), "shell-lint"],
        cwd=repo,
        env=_env(tmp, bin_, "2"),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=patience(120),
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return argv.read_text()


def test_dataflow_analysis_is_off_at_the_default_warning_severity(repo, tmp_path):
    assert "--extended-analysis=false" in _argv_at(repo, tmp_path, None)


def test_dataflow_analysis_stays_on_below_warning_where_its_notes_count(repo, tmp_path):
    argv = _argv_at(repo, tmp_path, "GOH_SHELL_LINT_SEVERITY=info\n")
    assert "--severity=info" in argv, argv
    assert "--extended-analysis" not in argv, argv
