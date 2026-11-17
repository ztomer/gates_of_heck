"""One Python prose mask for the Python and the Rust scanners (GOH.SCREENDOCMASK).

`checks/_prose_mask.py` (no_display_seam) and `goh screen` (no_screen_presentation) must agree on
every file of `tests/fixtures/prose_mask`: a `*_prose.py` names `pyautogui` only in a docstring,
comment or heredoc-shaped string statement and passes both; a `*_code.py` names it in code (an
import, a call, an argument string, a file that does not tokenize) and fails both.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "prose_mask"
sys.path.insert(0, str(ROOT / "checks"))
from _prose_mask import python_code_only  # noqa: E402

# A `.py.in` is a Python file that must not tokenize (so no formatter may read it); it is judged
# under its name without `.in`.
CASES = sorted(p.name for p in [*FIXTURES.glob("*.py"), *FIXTURES.glob("*.py.in")])
API = re.compile(r"\bpyautogui\b")


def _expected_code(name: str) -> bool:
    name = name.removesuffix(".in")
    assert name.endswith(("_prose.py", "_code.py")), name
    return name.endswith("_code.py")


def test_the_fixture_set_has_both_kinds() -> None:
    kinds = {_expected_code(n) for n in CASES}
    assert kinds == {True, False}
    assert len(CASES) >= 6


@pytest.mark.parametrize("name", CASES)
def test_python_mask_judges_the_fixture(name: str) -> None:
    text = (FIXTURES / name).read_text()
    assert bool(API.search(python_code_only(text))) is _expected_code(name)


@pytest.mark.parametrize("name", CASES)
def test_goh_screen_judges_the_fixture_the_same_way(goh: Path, tmp_path: Path, name: str) -> None:
    fast_init(tmp_path)
    shutil.copy(FIXTURES / name, tmp_path / name.removesuffix(".in"))
    r = subprocess.run(
        [str(goh), "screen", name.removesuffix(".in")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=dict(os.environ, GOH_DIR=str(ROOT)),
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert "not a git repo" not in r.stdout
    assert r.returncode == (1 if _expected_code(name) else 0), r.stdout + r.stderr
