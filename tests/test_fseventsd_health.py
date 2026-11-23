"""A saturated fseventsd is named as a fault after a whole suite, with its restart (GOH.FSEVCHURN).

2026-11-23: fseventsd sat at 6.9 GB RSS and 105.7% CPU after 2 d 18 h, and the suite's fs churn
tests had read it only as red or unmeasured; the owner restarted it by hand (10 MB fresh). Every
whole run now ends with one line about the daemon every file event costs: quiet under the bound, a
`✗` naming the RSS, the bound and the restart command over it (restarting it stays the owner's).
"""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import fseventsd_health as fh


def test_under_the_bound_is_quiet_and_over_it_is_a_fault_with_its_restart() -> None:
    assert fh.verdict(10_240) is None
    line = fh.verdict(6_900 * 1024)
    assert line is not None and line.startswith("✗"), line
    assert "6.7 GB" in line and "1.0 GB" in line, line
    assert fh.RESTART in line, line


def test_the_bound_is_one_gigabyte_unless_named() -> None:
    assert fh.LIMIT_KB == 1024 * 1024
    assert fh.verdict(2048, limit_kb=1024) is not None


def _cli(**env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "fseventsd_health.py")],
        capture_output=True,
        text=True,
        env=hermetic_env(**env),
        timeout=patience(30),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_cli_reads_the_seam_and_names_the_fault_on_stderr() -> None:
    r = _cli(GOH_FSEVENTSD_RSS_KB=str(3 * 1024 * 1024))
    assert r.returncode == 1, r
    assert "fseventsd" in r.stderr and fh.RESTART in r.stderr, r.stderr
    quiet = _cli(GOH_FSEVENTSD_RSS_KB="10240")
    assert quiet.returncode == 0 and quiet.stderr == "", quiet


def test_no_daemon_is_said_not_judged() -> None:
    r = _cli(GOH_FSEVENTSD_RSS_KB="none")
    assert r.returncode == 0, r
    assert r.stderr == "", r.stderr


def test_the_suite_runner_asks_after_a_whole_run() -> None:
    text = (REPO_ROOT / "tools" / "pytest.sh").read_text()
    assert "lib/fseventsd_health.py" in text, "a whole run never looks at fseventsd"
