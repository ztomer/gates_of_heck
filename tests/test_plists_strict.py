"""Every plist goh ships parses with a STRICT XML parser (G15).

plutil forgives a comment holding a double hyphen, which XML forbids; launchd is not promised the
same leniency, and `tools/install_launchagent.sh` lints with plutil, so nothing red noticed the
two plists that carried one. The strict parse is the structural check: expat refuses `--` in a
comment, and plistlib refuses a document that is not a plist.
"""

from __future__ import annotations

import plistlib
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from conftest import REPO_ROOT


def shipped_plists() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.plist"], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=True,
    ).stdout  # fmt: skip
    return [REPO_ROOT / line for line in out.splitlines() if (REPO_ROOT / line).is_file()]


def strict_problem(path: Path) -> str | None:
    """Why a strict parser refuses the plist, or None."""
    data = path.read_bytes()
    try:
        ET.fromstring(data)
        plistlib.loads(data)
    except (ET.ParseError, plistlib.InvalidFileException, ValueError) as exc:
        return f"{path.name}: {exc}"
    return None


def test_goh_ships_plists() -> None:
    """The population is not empty: a check over zero files reports compliance over nothing."""
    assert len(shipped_plists()) >= 2


@pytest.mark.parametrize("path", shipped_plists(), ids=lambda p: p.name)
def test_every_shipped_plist_parses_strictly(path: Path) -> None:
    assert strict_problem(path) is None, strict_problem(path)


def test_a_double_hyphen_comment_is_caught(tmp_path: Path) -> None:
    """Calibration: the check goes red on the exact defect it exists for."""
    bad = tmp_path / "bad.plist"
    bad.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>\n'
        "<!-- a note -- with a double hyphen -->\n"
        "<key>Label</key><string>x</string></dict></plist>\n"
    )
    assert strict_problem(bad) is not None
    good = tmp_path / "good.plist"
    good.write_text(bad.read_text().replace(" -- ", " - "))
    assert strict_problem(good) is None
