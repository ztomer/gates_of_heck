"""`GOH_MAX_LINES=off` is a recorded decision, not an omission — in both tiers.

The skills corpus leaves the cap off on purpose (its references/ ARE the long
catalogue) and was warned "cap not set" on every commit: a warning that fires on a
decision trains the reader to skip the line. `off` turns the warning into one info
line; an absent key still warns; neither tier runs the length step or hands
`--max off` to a checker.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL = ROOT / "gates" / "structural.sh"
WARNING = "file-length cap not set"
DECLARED = "file-length cap off — declared in .gatesrc (GOH_MAX_LINES=off)"


def _repo(tmp_path: Path, gatesrc: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gatesrc").write_text(gatesrc)
    (tmp_path / "long.py").write_text("x = 1\n" * 50)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _run(tier: str, goh: Path, repo: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_") or k == "GOH_LIVE"}
    env["GOH_DIR"] = str(ROOT)
    if tier == "python":
        env["GOH_NO_NATIVE"] = "1"
        cmd = ["bash", str(STRUCTURAL)]
    else:
        cmd = [str(goh), "structural"]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.parametrize("tier", ["python", "native"])
def test_off_is_an_info_line_not_a_warning(goh: Path, tmp_path: Path, tier: str) -> None:
    r = _run(tier, goh, _repo(tmp_path, "GOH_MAX_LINES=off\n"))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert DECLARED in r.stdout and WARNING not in out, out
    assert "file length <=" not in out, out


@pytest.mark.parametrize("tier", ["python", "native"])
def test_an_absent_cap_still_warns(goh: Path, tmp_path: Path, tier: str) -> None:
    r = _run(tier, goh, _repo(tmp_path, "# no keys\n"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert WARNING in r.stderr and DECLARED not in r.stdout, r.stdout + r.stderr


@pytest.mark.parametrize("tier", ["python", "native"])
def test_a_numeric_cap_still_bites(goh: Path, tmp_path: Path, tier: str) -> None:
    r = _run(tier, goh, _repo(tmp_path, "GOH_MAX_LINES=10\n"))
    assert r.returncode != 0 and "long.py" in r.stdout + r.stderr, r.stdout + r.stderr
