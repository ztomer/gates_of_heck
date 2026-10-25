"""The Python gates' runner is decided by TRACKED state, so the commit and push gates agree (rule 14).

`gates/_py.sh` chose `.venv/bin/python` when it existed, else python3. The push gate runs in a clean
export of the pushed commit, where the gitignored `.venv` does not exist, so the same commit was
judged by the project's venv at commit and by system python3 at push. token-pool, 2026-10-25: the
commit gate passed and the push gate died in pytest collection on `No module named 'mcp'`, a
finding about the code read off a missing environment. Now:

- a tracked `uv.lock` makes the runner `uv run --frozen`, which builds the toolchain from the lock
  identically in a working copy, a push export and a fresh clone;
- a push export whose source checkout had a `.venv` the export lacks, with no lock and no
  GOH_PY_RUNNER to rebuild it from, is refused with the fix named, instead of silently judging
  with another interpreter.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, stage, write

PY_SH = REPO_ROOT / "gates" / "_py.sh"
TUI = REPO_ROOT / "tui" / "lib.sh"


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _resolve(repo: Path, *, uv: str | None = "3.15", env: dict[str, str] | None = None):
    """Source _py.sh in `repo` with a fake python3 (3.15) and, unless uv is None, a fake uv whose
    `run ... python -c` reports `uv` as its version and which logs its argv."""
    bindir = repo.parent / f"bin-{repo.name}"
    _exe(bindir / "python3", '[ "$1" = "-c" ] && echo 3.15\nexit 0\n')
    if uv is not None:
        _exe(bindir / "uv", f'echo "$@" >>"{bindir}/uv.log"\n'
                            f'case "$*" in *" -c "*) echo {uv} ;; esac\nexit 0\n')  # fmt: skip
    script = f'. "{TUI}"; GOH_NAME=test; . "{PY_SH}"; goh_py_runner; echo "RUN=$RUN"'
    full = {k: v for k, v in os.environ.items()
            if k not in {"GOH_PY_RUNNER", "GOH_PYTHON_VERSION", "GOH_CROSS_REPO_ROOT"}}  # fmt: skip
    # Only the fake bin and the system dirs: a real uv elsewhere on PATH must not answer for it.
    full.update({"PATH": f"{bindir}:/usr/bin:/bin", **(env or {})})
    return subprocess.run(
        ["/bin/bash", "-c", script], cwd=repo, capture_output=True, text=True, env=full,
        check=False, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def _lock(repo: Path) -> None:
    write(repo, "uv.lock", "version = 1\n")
    stage(repo, "uv.lock")


def _venv(repo: Path, minor: str = "3.15") -> None:
    _exe(repo / ".venv" / "bin" / "python", f'[ "$1" = "-c" ] && echo {minor}\nexit 0\n')


def test_a_tracked_lock_without_a_venv_runs_through_uv(repo: Path) -> None:
    _lock(repo)
    r = _resolve(repo)
    assert r.returncode == 0, r.stderr
    assert "RUN=uv run --frozen" in r.stdout
    assert "python: uv run --frozen (3.15)" in r.stdout + r.stderr  # the version is read back


def test_a_tracked_lock_wins_over_a_present_venv(repo: Path) -> None:
    # Same runner in the working copy (venv present) as in the push export (venv absent).
    _lock(repo)
    _venv(repo, "3.13")
    r = _resolve(repo)
    assert r.returncode == 0, r.stderr
    assert "RUN=uv run --frozen" in r.stdout


def test_an_untracked_lock_is_not_the_repos_declaration(repo: Path) -> None:
    write(repo, "uv.lock", "version = 1\n")
    r = _resolve(repo)
    assert r.returncode == 0, r.stderr
    assert "RUN=uv" not in r.stdout


def test_a_tracked_lock_without_uv_is_refused_by_name(repo: Path) -> None:
    _lock(repo)
    r = _resolve(repo, uv=None)
    assert r.returncode != 0, r.stdout
    assert "uv.lock" in r.stderr and "uv" in r.stderr


def test_goh_py_runner_still_wins(repo: Path) -> None:
    _lock(repo)
    r = _resolve(repo, env={"GOH_PY_RUNNER": "python3 -m"})
    assert r.returncode == 0, r.stderr
    assert "RUN=python3 -m" in r.stdout


def test_the_pin_reaches_uv_and_is_read_back(repo: Path) -> None:
    _lock(repo)
    r = _resolve(repo, uv="3.14", env={"GOH_PYTHON_VERSION": "3.14"})
    assert r.returncode == 0, r.stderr
    assert "RUN=uv run --frozen --python 3.14" in r.stdout


def test_uv_resolving_another_version_than_the_pin_is_refused(repo: Path) -> None:
    _lock(repo)
    write(repo, ".python-version", "3.14\n")
    stage(repo, ".python-version")
    r = _resolve(repo, uv="3.15")
    assert r.returncode != 0, r.stdout
    assert "3.14" in r.stderr and "3.15" in r.stderr


def _export(source: Path, tmp_path: Path) -> Path:
    """A push export of `source`: a worktree of its pushed commit, as push_gate.sh makes one."""
    write(source, ".gitignore", ".venv/\n")  # as in every real repo: the export lacks it
    commit_all(source)
    export = tmp_path / "export"
    git(source, "worktree", "add", "-q", "--detach", str(export))
    return export


def test_an_export_that_lost_its_venv_is_refused_with_the_fix(repo: Path, tmp_path: Path) -> None:
    _venv(repo)
    export = _export(repo, tmp_path)
    r = _resolve(export, env={"GOH_CROSS_REPO_ROOT": str(repo)})
    assert r.returncode != 0, r.stdout
    assert ".venv" in r.stderr and "uv.lock" in r.stderr and "GOH_PY_RUNNER" in r.stderr


def test_an_inherited_source_that_is_another_repo_decides_nothing(
    repo: Path, tmp_path: Path
) -> None:
    # The outer push gate's value reaches every fixture repo goh's own suite gates; a fixture is
    # not an export of goh's checkout, so goh's .venv must not refuse it (push of 1ee22aa).
    other = tmp_path / "other"
    _venv(other)
    r = _resolve(repo, env={"GOH_CROSS_REPO_ROOT": str(other)})
    assert r.returncode == 0, r.stderr


def test_an_export_whose_source_had_no_venv_is_judged_as_before(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    r = _resolve(repo, env={"GOH_CROSS_REPO_ROOT": str(tmp_path / "source")})
    assert r.returncode == 0, r.stderr
    assert "RUN=" in r.stdout and "uv run" not in r.stdout


def test_an_export_with_a_lock_needs_no_venv(repo: Path, tmp_path: Path) -> None:
    _venv(repo)
    _lock(repo)
    export = _export(repo, tmp_path)
    r = _resolve(export, env={"GOH_CROSS_REPO_ROOT": str(repo)})
    assert r.returncode == 0, r.stderr
    assert "RUN=uv run --frozen" in r.stdout
