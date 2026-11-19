"""GOH.LOADWHY (2026-11-19): `tools/load.py` makes no spinner without a reason.

THE CLASS. "Load only via tools/load.py" read, to lanes, as "run your tests under `load.py N`":
`load.py --wait --max 1h 12 -- tools/affected_tests.py --run` put twelve busy loops beside a test
run that needed none, with three lands and four lanes already on the box (load ~110 on 16 cores).
Nothing told admission (N=0) from making load, so a lane picked N by habit. The tool now REFUSES
N>0 without `--why <the timing defect this load reproduces>`; N=0 needs none. The why is written
to the registry and the baseline line, and `goh agent-stalls` and the SessionStart advisories name
every live spinner run with its why and its owner's directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import timing_bounds
from conftest import REPO_ROOT, hermetic_env
from load_kit import TOOL, WHY

sys.path.insert(0, str(REPO_ROOT / "lib"))
import load_live
import load_registry as reg

STALLS = REPO_ROOT / "hooks" / "claude" / "agent_stalls.py"
ADVISORIES = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"

pytestmark = pytest.mark.xdist_group("load_orphans")

REASON = "the flaky wait only reproduces on a loaded box"


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timing_bounds.patience(60.0),
        check=False,
        **kwargs,
    )  # fmt: skip


@pytest.mark.parametrize("why", [[], ["--why", ""], ["--why", "   "], ["--why", "tests"]])
def test_spinners_without_a_why_are_refused_before_any_is_started(
    tmp_path: Path, why: list[str]
) -> None:
    marker = tmp_path / "ran"
    result = _run(["--dir", str(tmp_path), *why, "2", "--", "/bin/sh", "-c", f"touch {marker}"])
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "--why" in result.stderr, result.stderr
    assert " 0 -- " in result.stderr or "N=0" in result.stderr, "the refusal names admission"
    assert not marker.exists(), "the command ran under a load nobody explained"
    assert "started" not in result.stdout, "a spinner was started"
    assert not list(tmp_path.glob("*.json")), "a registry was written for a refused run"


def test_admission_needs_no_why(tmp_path: Path) -> None:
    result = _run(["--dir", str(tmp_path), "0", "--", "/bin/sh", "-c", "exit 0"])
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_the_why_is_in_the_registry_and_the_baseline_line(tmp_path: Path) -> None:
    """The command reads the registry the tool wrote for it, while its spinners are up."""
    dump = tmp_path / "seen.json"
    result = _run(
        [
            "--dir", str(tmp_path), "--why", REASON, "1", "--",
            "/bin/sh", "-c", f"cp {tmp_path}/registry.json {dump}",
        ],
        cwd=tmp_path,
    )  # fmt: skip
    assert result.returncode == 0, (result.stdout, result.stderr)
    seen = json.loads(dump.read_text())
    assert seen["why"] == REASON, seen
    assert seen["cwd"] == str(tmp_path.resolve()), seen
    baseline = [ln for ln in (result.stdout + result.stderr).splitlines() if "baseline" in ln]
    assert baseline and REASON in baseline[0], baseline


def _registry(directory: Path, **extra) -> Path:
    path = reg.registry_path(directory)
    pid = os.getpid()
    data = {
        "version": reg.VERSION,
        "owner_pid": pid,
        "owner_start": reg.pid_start(pid) or "",
        "started_at": 1000.0,
        "max_s": 3600.0,
        "deadline_at": 4600.0,
        "command": ["python3", "tools/affected_tests.py", "--run"],
        "pids": [{"pid": pid, "start": reg.pid_start(pid) or ""}] * 12,
    }
    path.write_text(json.dumps(data | extra))
    return path


def test_a_live_spinner_run_is_named_with_its_why_and_owner_directory(tmp_path: Path) -> None:
    _registry(tmp_path, why=REASON, cwd="/work/lane-x")
    (line,) = load_live.lines(tmp_path, now=1000.0 + 14 * 60)
    for part in ("12 spinners", "14m", REASON, "/work/lane-x", f"pid {os.getpid()}"):
        assert part in line, (part, line)


def test_a_run_recorded_without_a_why_says_so(tmp_path: Path) -> None:
    _registry(tmp_path)
    (line,) = load_live.lines(tmp_path, now=1100.0)
    assert "no why recorded" in line, line


def test_a_run_whose_owner_is_gone_is_not_live(tmp_path: Path) -> None:
    _registry(tmp_path, owner_pid=2**22 + 12345, owner_start="")
    assert load_live.lines(tmp_path, now=1100.0) == []


def test_an_empty_or_missing_directory_names_nothing(tmp_path: Path) -> None:
    assert load_live.lines(tmp_path, now=1.0) == []
    assert load_live.lines(tmp_path / "absent", now=1.0) == []


@pytest.mark.parametrize("script", [STALLS, ADVISORIES], ids=["agent-stalls", "session-start"])
def test_agent_stalls_and_the_session_start_line_name_a_live_run(
    tmp_path: Path, script: Path
) -> None:
    load = tmp_path / "load"
    load.mkdir()
    _registry(load, why=REASON, cwd="/work/lane-x", started_at=1.0)
    argv = [sys.executable, str(script)]
    if script == STALLS:
        argv += ["--procs", os.devnull, "--subagents", str(tmp_path / "none")]
    env = hermetic_env(GOH_LOAD_DIR=str(load), HOME=str(tmp_path))
    done = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
        timeout=timing_bounds.patience(60.0), check=False,
    )  # fmt: skip
    out = done.stdout
    assert REASON in out and "/work/lane-x" in out and "12 spinners" in out, (out, done.stderr)


def test_the_kit_passes_a_why_so_the_suite_never_runs_a_load_nobody_explained() -> None:
    assert WHY[0] == "--why" and len(WHY[1].split()) >= 3


def test_the_hooks_door_says_nothing_for_a_load_directory_it_may_not_read(
    tmp_path: Path, monkeypatch
) -> None:
    """A load directory inside a work tree is refused (GOH.STRAYLOCK); a hook never fails for it."""
    (tmp_path / ".git").mkdir()  # what makes a directory a work tree to reg.checked
    monkeypatch.setenv("GOH_LOAD_DIR", str(tmp_path / "load"))
    sys.path.insert(0, str(REPO_ROOT / "hooks" / "claude"))
    import load_runs

    assert load_runs.lines() == []
