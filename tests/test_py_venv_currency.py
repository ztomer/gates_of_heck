"""A stale project venv cannot silently become the interpreter a Python gate judges with (1.6).

`gates/_py.sh` prefers `.venv/bin/python` over `python3`. A venv is a snapshot of whichever python3
built it, so one that lags judged ruff and pytest on the older interpreter, unsaid. The choice is
now named with its version, and a lagging venv is refused unless the repo declares the pin.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, stage, write

PY_SH = REPO_ROOT / "gates" / "_py.sh"
TUI = REPO_ROOT / "tui" / "lib.sh"


def _fake_python(path: Path, minor: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\n[ "$1" = "-c" ] && echo {minor}\nexit 0\n')
    path.chmod(0o755)


def _resolve(repo: Path, venv: str | None, system: str, env: dict[str, str] | None = None):
    """Source _py.sh in `repo` with a fake python3 of `system` on PATH and, if given, a fake venv."""
    bindir = repo.parent / f"bin-{repo.name}"
    _fake_python(bindir / "python3", system)
    if venv is not None:
        _fake_python(repo / ".venv" / "bin" / "python", venv)
    script = f'. "{TUI}"; GOH_NAME=test; . "{PY_SH}"; goh_py_runner; echo "RUN=$RUN"'
    full = {k: v for k, v in os.environ.items() if k != "GOH_PY_RUNNER"}
    full.update({"PATH": f"{bindir}:{os.environ['PATH']}", **(env or {})})
    return subprocess.run(
        ["/bin/bash", "-c", script],
        cwd=repo,
        capture_output=True,
        text=True,
        env=full,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_venv_older_than_python3_is_refused_and_both_versions_named(repo: Path) -> None:
    r = _resolve(repo, "3.13", "3.15")
    assert r.returncode != 0, r.stdout
    assert "3.13" in r.stderr and "3.15" in r.stderr, r.stderr


def test_a_committed_python_version_pins_the_venv(repo: Path) -> None:
    write(repo, ".python-version", "3.13\n")
    stage(repo, ".python-version")
    r = _resolve(repo, "3.13", "3.15")
    assert r.returncode == 0, r.stderr
    assert "RUN=.venv/bin/python -m" in r.stdout
    assert "python: .venv/bin/python (3.13)" in r.stdout + r.stderr


def test_an_untracked_python_version_is_not_the_repos_pin(repo: Path) -> None:
    write(repo, ".python-version", "3.13\n")
    r = _resolve(repo, "3.13", "3.15")
    assert r.returncode != 0, r.stdout


def test_a_pin_naming_another_minor_does_not_excuse_the_venv(repo: Path) -> None:
    write(repo, ".python-version", "3.12\n")
    stage(repo, ".python-version")
    r = _resolve(repo, "3.13", "3.15")
    assert r.returncode != 0, r.stdout


def test_goh_py_runner_is_a_declared_pin(repo: Path) -> None:
    r = _resolve(repo, "3.13", "3.15", {"GOH_PY_RUNNER": "python3 -m"})
    assert r.returncode == 0, r.stderr
    assert "RUN=python3 -m" in r.stdout


def test_a_current_venv_is_used_and_named(repo: Path) -> None:
    r = _resolve(repo, "3.15", "3.15")
    assert r.returncode == 0, r.stderr
    assert "python: .venv/bin/python (3.15)" in r.stdout + r.stderr


def test_a_newer_venv_is_not_stale(repo: Path) -> None:
    r = _resolve(repo, "4.0", "3.15")
    assert r.returncode == 0, r.stderr
