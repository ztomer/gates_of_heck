"""A failed step's line names HOW it failed: its exit status, or the signal that ended it, or its
timeout (GOH.GATEHONEST part 1).

token-pool's push failed in `swift test` with no test output -- the process died after `Build
complete` -- and the gate's line said only `failed (command: ...)`. Whether that was a signal, an
OOM kill (jetsam sends SIGKILL) or an exit code was recorded nowhere, so the cause could not be
named. Both runners are pinned: goh_step (gates/_common.sh) and local_ci.sh's `FAILED:` line, which
is what the pre-push log keeps. And the Python step wrapper must not mangle a signal: a child ended
by SIGKILL has `returncode == -9`, and `sys.exit(-9)` is status 247, which names nothing; the shell
convention (and the Rust twin, stepcmd.rs) is 128+N.
"""

import os
import subprocess
import textwrap
from pathlib import Path

from conftest import REPO_ROOT

SELF_KILL = "kill -KILL $$"


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "GOH_RESOLVED_BIN"}
    env.update(extra)
    return env


def test_the_python_wrapper_reports_a_signal_as_128_plus_n() -> None:
    r = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "lib" / "bounded_run.py"),
            "--timeout",
            "30",
            "--",
            "/bin/bash",
            "-c",
            SELF_KILL,
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert r.returncode == 137, f"SIGKILL came back as {r.returncode}, not 128+9"


def _goh_step(tmp_path: Path, cmd: str, **env: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init reason
        goh_step "the step" /bin/bash -c '{cmd}'
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
        env=_env(**env),
    )


def _fail_line(stderr: str) -> str:
    lines = [ln for ln in stderr.splitlines() if "the step failed" in ln]
    assert lines, f"no failure line:\n{stderr}"
    return lines[-1]


def test_goh_step_names_the_signal(tmp_path: Path) -> None:
    line = _fail_line(_goh_step(tmp_path, SELF_KILL).stderr)
    assert "SIGKILL" in line and "137" in line, line


def test_goh_step_names_the_exit_status(tmp_path: Path) -> None:
    line = _fail_line(_goh_step(tmp_path, "exit 3").stderr)
    assert "exit status 3" in line, line


def test_goh_step_names_the_timeout(tmp_path: Path) -> None:
    r = _goh_step(tmp_path, "sleep 20", GOH_STEP_TIMEOUT="1", GOH_STEP_GRACE="1")
    line = _fail_line(r.stderr)
    assert "timed out after 1s" in line, line


def _local_ci(tmp_path: Path, step: str) -> subprocess.CompletedProcess:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / ".gatesrc").write_text("")
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "local_ci.sh"), "--step", step],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=_env(),
        timeout=120,
    )


def _ci_line(r: subprocess.CompletedProcess) -> str:
    out = r.stdout + r.stderr
    lines = [ln for ln in out.splitlines() if "] FAILED" in ln]
    assert lines, f"no FAILED line:\n{out}"
    return lines[0]


def test_local_ci_names_the_signal(tmp_path: Path) -> None:
    line = _ci_line(_local_ci(tmp_path, f"bash -c '{SELF_KILL}'"))
    assert "SIGKILL" in line, line


def test_local_ci_names_the_exit_status(tmp_path: Path) -> None:
    line = _ci_line(_local_ci(tmp_path, "exit 3"))
    assert "exit status 3" in line, line
