"""A gate runs a pinned interpreter with its own import path, never the ambient `python3` (6.2).

Homebrew moved `python3` to 3.15 on 2026-10-10 and three repos' gates broke three ways at once:
necrohand lost spacy, scripts' coverage hook moved, and ZeroThunder's commit hook failed `No module
named 'tui'` unless `PYTHONPATH` was set. `gates/python.sh` runs the repo's pinned version (read
back, never a keg path) with the house libraries on its path and the shell's `PYTHONPATH` dropped;
the structural step "no bare python in a hook or gate" refuses a hook that goes around it. The
shape table is `crates/goh/src/barepy/tests.rs`; this file is the seam and the step end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, commit_all, git, hermetic_env, write

SEAM = REPO_ROOT / "gates" / "python.sh"
LABEL = "no bare python in a hook or gate"
IMPORT_TUI = "import tui.lib, sys; print('tui from', sys.version_info[:2])"


def _seam(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    run_env = hermetic_env(GOH_DIR=str(REPO_ROOT), GOH_LIVE="1", **env)
    run_env.pop("PYTHONPATH", None)
    return subprocess.run(
        ["bash", str(SEAM), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=run_env,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _minor(python: str) -> str:
    return subprocess.run(
        [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def test_a_bare_python3_in_a_hook_cannot_import_the_house_library(repo: Path) -> None:
    """The baseline the seam answers: with PYTHONPATH unset, `import tui` fails."""
    env = hermetic_env()
    env.pop("PYTHONPATH", None)
    r = subprocess.run(
        ["python3", "-c", IMPORT_TUI],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode != 0 and "No module named 'tui'" in r.stderr, r.stdout + r.stderr


def test_through_the_seam_the_house_library_imports(repo: Path) -> None:
    r = _seam(repo, "-c", IMPORT_TUI)
    assert r.returncode == 0 and "tui from" in r.stdout, r.stdout + r.stderr


def test_the_shells_pythonpath_does_not_reach_the_gate(repo: Path, tmp_path: Path) -> None:
    shadow = tmp_path / "shadow"
    (shadow / "tui").mkdir(parents=True)
    (shadow / "tui" / "__init__.py").write_text("raise SystemExit('the shell PYTHONPATH won')\n")
    r = _seam(repo, "-c", IMPORT_TUI, PYTHONPATH=str(shadow))
    assert r.returncode == 0 and "PYTHONPATH won" not in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_committed_pin_runs_that_version_read_back(repo: Path) -> None:
    pin = _minor("python3")
    write(repo, ".python-version", f"{pin}\n")
    commit_all(repo)
    r = _seam(repo, "-c", "import sys; print('%d.%d' % sys.version_info[:2])")
    assert r.returncode == 0 and r.stdout.strip() == pin, r.stdout + r.stderr


def test_a_pin_nothing_installed_satisfies_is_refused_by_name(repo: Path) -> None:
    r = _seam(repo, "-c", "print(1)", GOH_PYTHON_VERSION="3.1")
    assert r.returncode != 0, r.stdout
    assert "GOH_PYTHON_VERSION pins Python 3.1" in r.stderr, r.stderr
    assert "uv python install 3.1" in r.stderr, r.stderr


def test_a_venv_that_is_not_the_pin_is_refused(repo: Path, tmp_path: Path) -> None:
    """A venv is a snapshot of whichever python built it: under a pin, it must be the pin's."""
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    fake = venv / "python"
    fake.write_text('#!/bin/sh\necho "3.9.0"\n')
    fake.chmod(0o755)
    r = _seam(repo, "-c", "print(1)", GOH_PYTHON_VERSION="3.14")
    assert r.returncode != 0 and "is 3.9.0" in r.stderr, r.stdout + r.stderr


def _structural(goh: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(goh), "structural", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_a_bare_python3_in_the_gate_is_refused_at_commit(goh: Path, repo: Path) -> None:
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nset -e\npython3 tools/x.py --staged\n")
    git(repo, "add", "tools/gate.sh")
    r = _structural(goh, repo, "--staged")
    assert r.returncode == 1 and f"structural: {LABEL}" in r.stderr, r.stdout + r.stderr
    assert "tools/gate.sh:3: python3" in r.stderr and "gates/python.sh" in r.stderr, r.stderr


def test_a_hook_through_the_seam_passes(goh: Path, repo: Path) -> None:
    write(repo, ".githooks/pre-commit", '#!/bin/sh\nbash "$GOH/gates/python.sh" tools/x.py\n')
    write(repo, "tools/other.sh", "#!/bin/sh\npython3 not_a_hook.py\n")
    git(repo, "add", ".githooks/pre-commit", "tools/other.sh")
    r = _structural(goh, repo, "--staged")
    assert LABEL in r.stdout + r.stderr and f"structural: {LABEL}" not in r.stderr, (
        r.stdout + r.stderr
    )


def test_an_existing_bare_python3_fails_at_full_scope(goh: Path, repo: Path) -> None:
    write(repo, ".githooks/pre-push", "#!/bin/sh\nexec python3 tools/push.py\n")
    commit_all(repo, "an old bare python3")
    r = _structural(goh, repo, "--full")
    assert r.returncode == 1 and ".githooks/pre-push:2: python3" in r.stderr, r.stdout + r.stderr
