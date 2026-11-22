"""The floor refusal names the fix by how the repo resolves python (GOH.FLOORHINTUV, GOH.VENVDRIFT).

A pip line does not move a frozen uv lock, and a uv project's .venv behind its own lock needs a
sync, not a pip install into python3. The gate's interpreter prefix (`sys.prefix`) says which.
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "gates"))
import required_judge

ROW = {"tool": "pytest", "kind": "pip", "pin": "", "floor": "9.2.0",
       "install": "python3 -m pip install pytest==9.2.0"}  # fmt: skip


def _uv_project(tmp_path: Path, locked: str) -> Path:
    (tmp_path / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "pytest"\nversion = "{locked}"\n\n'
        '[[package]]\nname = "pluggy"\nversion = "1.6.0"\n')  # fmt: skip
    (tmp_path / ".venv").mkdir()
    return tmp_path / ".venv"


def _refusal(monkeypatch, prefix: str) -> str:
    monkeypatch.setattr(required_judge, "_prefix", lambda: prefix)
    why = required_judge.judge(ROW, "pytest 9.1.1")
    assert why is not None
    return why


def test_a_frozen_lock_below_the_floor_names_uv_lock_upgrade(tmp_path, monkeypatch) -> None:
    why = _refusal(monkeypatch, str(_uv_project(tmp_path, "9.1.1")))
    assert "uv lock --upgrade-package pytest" in why and str(tmp_path) in why, why
    assert "pip install" not in why, why


def test_a_venv_behind_a_lock_at_the_floor_names_uv_sync(tmp_path, monkeypatch) -> None:
    why = _refusal(monkeypatch, str(_uv_project(tmp_path, "9.2.0")))
    assert "uv sync --frozen" in why and str(tmp_path) in why and "9.2.0" in why, why
    assert "pip install" not in why, why


def test_a_plain_interpreter_keeps_the_pip_line(tmp_path, monkeypatch) -> None:
    why = _refusal(monkeypatch, str(tmp_path))
    assert "python3 -m pip install pytest==9.2.0" in why, why
