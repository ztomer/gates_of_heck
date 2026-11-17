"""The registries .gitattributes merges by row (`merge=goh-rows`, GOH.LANDUNION) hold each key once.

A merge by row is only sound while a key names one line: a hand merge or a `merge=union` that
keeps both sides of one edited row leaves the key twice, which `lib/row_merge.py --check` names.
Checked at commit over the index (the directive below) and with the suite at push and land.
docs/config.md held GOH_STEP_TIMEOUT and GOH_STEP_GRACE twice, in two sections, each saying
something the other did not.
"""

# goh-reads-staged: .gitattributes docs/map.md docs/config.md gates/verdict_free_keys.txt
# goh-reads-staged: crates/goh/house_scripts.tsv

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT

DRIVER = "goh-rows"


def _row_merged() -> list[str]:
    """Every path .gitattributes gives `merge=goh-rows`, read from the file itself."""
    out = []
    for line in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        words = line.split()
        if words and not words[0].startswith("#") and f"merge={DRIVER}" in words[1:]:
            out.append(words[0])
    return out


def _check(*paths: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "row_merge.py"), "--check", *paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_the_registries_are_row_merged() -> None:
    assert sorted(_row_merged()) == sorted(
        [
            "docs/map.md",
            "docs/config.md",
            "gates/verdict_free_keys.txt",
            "crates/goh/house_scripts.tsv",
        ]
    )


def test_every_row_merged_registry_holds_each_key_once() -> None:
    paths = _row_merged()
    assert all((REPO_ROOT / p).is_file() for p in paths), paths
    out = _check(*paths)
    assert out.returncode == 0, out.stderr


def test_a_key_on_two_lines_is_named(tmp_path) -> None:
    """Calibration: the check sees a repeated table key, bullet and word, and no header row."""
    md = tmp_path / "m.md"
    md.write_text(
        "| Key | V |\n|---|---|\n| `K` | 1 |\n| `K` | 2 |\n\n| Key | V |\n|---|---|\n"
        "* `t.sh` -- a.\n* `t.sh` -- b.\n",
        encoding="utf-8",
    )
    words = tmp_path / "w.txt"
    words.write_text("# c\n# c\nGOH_X\nGOH_Y\nGOH_X\n", encoding="utf-8")
    out = _check(str(md), str(words))
    assert out.returncode == 1
    assert out.stderr.splitlines() == [
        f"{md}: the key `K` is on two lines",
        f"{md}: the key `t.sh` is on two lines",
        f"{words}: the key GOH_X is on two lines",
    ]
