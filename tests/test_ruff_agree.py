"""Every way the machine runs ruff is the same ruff (GOH.TOOLHYGIENE, rule 17).

2026-11-11: `python3 -m ruff` was 0.17.0 (the house interpreter, the Python gates' runner) while
PATH's `ruff` was 0.17.4 (~/.local/bin, /opt/homebrew/bin: pyformat's fallback, what an agent
types). gates/ruff_agree.py refuses the split, naming each invocation and the upgrade.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

AGREE = REPO_ROOT / "gates" / "ruff_agree.py"


def _fake(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "ruff {version}"\n')
    path.chmod(0o755)
    return path


def _py(tmp_path: Path, version: str) -> Path:
    """An interpreter whose `-m ruff --version` says `version`."""
    return _fake(tmp_path / "py" / "python3", version)


def _run(tmp_path: Path, py: Path, *dirs: Path) -> subprocess.CompletedProcess[str]:
    env = hermetic_env(GOH_PY=str(py), PATH=os.pathsep.join([*map(str, dirs), "/usr/bin:/bin"]))
    return subprocess.run([sys.executable, str(AGREE)], env=env, capture_output=True, text=True,
                          timeout=60, stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_split_of_2026_11_11_is_refused_naming_both(tmp_path):
    local = _fake(tmp_path / "local" / "ruff", "0.17.4")
    r = _run(tmp_path, _py(tmp_path, "0.17.0"), local.parent)
    assert r.returncode == 1, r.stderr
    assert "ruff 0.17.0" in r.stderr and "ruff 0.17.4" in r.stderr, r.stderr
    assert "-m pip install -U ruff" in r.stderr, r.stderr  # the older one's upgrade


@pytest.mark.parametrize("path_versions", [(), ("0.17.4",), ("0.17.4", "0.17.4")])
def test_one_version_everywhere_agrees(tmp_path, path_versions):
    dirs = [_fake(tmp_path / f"d{i}" / "ruff", v).parent for i, v in enumerate(path_versions)]
    r = _run(tmp_path, _py(tmp_path, "0.17.4"), *dirs)
    assert r.returncode == 0, r.stderr


def test_two_rufs_on_path_that_disagree_are_refused(tmp_path):
    a = _fake(tmp_path / "a" / "ruff", "0.17.4").parent
    b = _fake(tmp_path / "b" / "ruff", "0.16.9").parent
    r = _run(tmp_path, _py(tmp_path, "0.17.4"), a, b)
    assert r.returncode == 1 and "ruff 0.16.9" in r.stderr, r.stderr


def test_the_suite_preflight_runs_it():
    assert "gates/ruff_agree.py" in (REPO_ROOT / "tools" / "pytest.sh").read_text()
