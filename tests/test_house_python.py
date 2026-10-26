"""The house interpreter is found ONE way, and an older one is refused by name (G2d, 2026-10-25).

Every goh script ran a bare `python3` -- the first on PATH. This suite hands many of its gates
PATH=/usr/bin:/bin, so those gates ran on macOS's /usr/bin/python3 3.9.6 (EOL since 2025-10) while
the suite itself ran on 3.15, and G2b read the green as "proven on 3.9" and floored python3 there.
Owner, asked whether the tests should be on 3.9 rather than 3.15: no -- "go ahead".

lib/house_python.sh is the seam: the NEWEST python3 on PATH or in a release's install dirs,
whatever the PATH order, judged against gates/required_tools.tsv's python3 floor. GOH_PY names one
exactly; GOH_PY_SEARCH restricts the search (a planted fake 3.9, or the real /usr/bin).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "lib" / "house_python.sh"
sys.path.insert(0, str(ROOT / "gates"))
import required_tools  # noqa: E402

FLOOR = required_tools.floors()["python3"]


def _fake(directory: Path, name: str, version: str) -> Path:
    """A `python3` that reports `version` the way the resolver asks (sys.version_info)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\necho {version}\n")
    path.chmod(0o755)
    return path


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"GOH_PY", "GOH_PY_OK", "GOH_PY_SEARCH"}}
    env.update(extra)
    return env


def _resolve(**extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(RESOLVER)],
        env=_env(**extra),
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )


def test_the_floor_is_the_house_release() -> None:
    assert FLOOR == "3.15"


def test_a_reduced_path_still_finds_the_house_interpreter() -> None:
    """PATH=/usr/bin:/bin is what the suite's gates and a GUI client's hooks get."""
    done = _resolve(PATH="/usr/bin:/bin")
    assert done.returncode == 0, done.stderr
    py = done.stdout.strip()
    version = subprocess.run(
        [py, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    assert required_tools.at_least(version, FLOOR), (py, version)
    assert py != "/usr/bin/python3"


def test_a_fake_3_9_through_the_search_is_refused_naming_the_floor_and_the_fix(
    tmp_path: Path,
) -> None:
    fake = _fake(tmp_path / "bin", "python3", "3.9.6")
    done = _resolve(GOH_PY_SEARCH=str(fake.parent))
    assert done.returncode != 0
    assert f"the house Python is {FLOOR} or newer" in done.stderr
    assert "3.9.6" in done.stderr and str(fake) in done.stderr
    assert "brew install python" in done.stderr and "GOH_PY" in done.stderr


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no system python3")
def test_the_real_usr_bin_python3_alone_is_refused() -> None:
    done = _resolve(GOH_PY_SEARCH="/usr/bin")
    assert done.returncode != 0, done.stdout
    assert f"the house Python is {FLOOR} or newer" in done.stderr


def test_an_explicit_goh_py_below_the_floor_is_refused(tmp_path: Path) -> None:
    fake = _fake(tmp_path / "bin", "python3", "3.9.6")
    done = _resolve(GOH_PY=str(fake))
    assert done.returncode != 0
    assert f"the house Python is {FLOOR} or newer" in done.stderr


def test_the_newest_wins_whatever_the_path_order(tmp_path: Path) -> None:
    old = _fake(tmp_path / "a", "python3", "3.14.9")
    new = _fake(tmp_path / "b", "python3.15", "3.15.1")
    for order in (f"{old.parent}:{new.parent}", f"{new.parent}:{old.parent}"):
        done = _resolve(GOH_PY_SEARCH=order)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == str(new), order


def test_a_gate_on_a_machine_with_only_an_older_python_fails_before_any_step(
    tmp_path: Path,
) -> None:
    """End to end: a real gate entry (gates/python.sh, the hooks' seam) under the planted 3.9."""
    fake = _fake(tmp_path / "bin", "python3", "3.9.6")
    done = subprocess.run(
        ["bash", str(ROOT / "gates" / "python.sh"), "-V"],
        env=_env(GOH_PY_SEARCH=str(fake.parent), GOH_LIVE="1"),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert done.returncode != 0, done.stdout
    assert f"the house Python is {FLOOR} or newer" in done.stderr
    assert "3.9.6" in done.stderr


def test_this_suite_runs_on_the_house_interpreter() -> None:
    assert required_tools.at_least("%d.%d" % sys.version_info[:2], FLOOR), sys.version
