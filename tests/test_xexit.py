"""A step that PRINTS a failure and EXITS 0 is red (GOH.XEXIT).

In the house TUI contract `✗` means failure and `⚠` means advisory. ZoneWM's census_bench void
hold and three render selftests printed a `✗` line and exited 0, so the gate called them green
(fixed there in ZoneWM 06ee510e). The class is a step whose words and exit code disagree; every
gate step passes through goh_step or local_ci.sh, so both refuse it, through ONE predicate
(lib/xexit.py). A `✗` QUOTED mid-line (a calibration plant's row) is not a status line.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import xexit


def _goh_step(tmp_path: Path, cmd: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init planted
        goh_step "planted step" /bin/bash -c {cmd!r}
        goh_done
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _local_ci(tmp_path: Path, cmd: str) -> subprocess.CompletedProcess:
    d = tmp_path / "proj"
    d.mkdir()
    (d / ".gatesrc").write_text("")
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh"), "--steps-only", "--step", cmd],
        cwd=d,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_predicate_names_status_lines_only():
    esc = chr(27)  # a colour escape the step printed, built so no literal sits in the source
    log = f"ok\n  {esc}[31m✗{esc}[0m void hold\nrow: ✗ quoted\n⚠ advisory\n✗ second\n"
    assert xexit.failure_lines(log) == ["✗ void hold", "✗ second"]
    assert xexit.failure_lines("⚠ advisory only\n✓ fine\n") == []


def test_goh_step_refuses_cross_at_exit_zero(tmp_path):
    r = _goh_step(tmp_path, "echo '✗ void hold 4411'; exit 0")
    assert r.returncode != 0, f"a ✗ at exit 0 passed:\n{r.stdout}{r.stderr}"
    assert "void hold 4411" in r.stderr and "exited 0" in r.stderr


def test_goh_step_passes_advisory_and_quoted_cross(tmp_path):
    r = _goh_step(tmp_path, "echo '⚠ advisory'; echo 'row: ✗ quoted'; exit 0")
    assert r.returncode == 0, r.stderr


def test_local_ci_refuses_cross_at_exit_zero(tmp_path):
    r = _local_ci(tmp_path, "echo '✗ render selftest 9902'; true")
    assert r.returncode != 0, f"a ✗ at exit 0 passed:\n{r.stdout}{r.stderr}"
    assert "render selftest 9902" in r.stdout + r.stderr


def test_local_ci_passes_advisory(tmp_path):
    r = _local_ci(tmp_path, "echo '⚠ advisory'; true")
    assert r.returncode == 0, r.stdout + r.stderr


def test_cr_progress_line_is_judged_by_its_last_segment():
    # a terminal shows the text after the last `\r`; a progress line ending in a `✗` is a status line
    assert xexit.failure_lines("...\r✗ x") == ["✗ x"]


def test_python_and_rust_twins_agree_on_the_shared_fixture():
    # ONE fixture, read here and by step_report.rs `xexit_tests` (GOH.XEXIT2): the twins split alike
    import json

    cases = json.loads((REPO_ROOT / "tests/fixtures/xexit/cases.json").read_text(encoding="utf-8"))
    assert cases
    for case in cases:
        assert xexit.failure_lines(case["text"]) == case["crosses"], case["name"]
