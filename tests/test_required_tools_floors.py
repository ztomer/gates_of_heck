"""A tool below its FLOOR is a named failure, for every consumer (G2b, 2026-10-25).

`gates/required_tools.tsv` lists every tool a gate refuses without; its `floor` column says the
OLDEST version each may be. A floor, never a pin: the house runs the newest release of every layer
("why pin goh by version? The whole idea is to have ever improving qa gates", owner, 2026-10-25),
so a tool NEWER than its floor is green, always, and only a missing tool or one OLDER than its
floor is red. The floors were seeded from sys_updater's `tools/gate-floors.env` (R3.29), which
carried each one's reason; the reasons now sit above the rows in the manifest. One seed was
corrected on evidence: python3 3.14 was "the oldest installed", and 3.9.6 was installed and runs
both this suite's gates and sys_updater's tests green.

The enforcement is goh's existing requirement check, `goh_require` (and `need` in the coverage
gate, `goh_py_require` for a module the Python gates run): present is no longer enough, it must be
at or above its floor. `gates/required_tools.py --check` judges a whole repo's layers the same
way, and `--floors` publishes the table for a consumer that records its own toolchain.

The seam is the real one: a fake tool first on PATH that reports a version.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "gates" / "required_tools.py"
COMMON = ROOT / "gates" / "_common.sh"


def _fake(bindir: Path, name: str, output: str) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    path = bindir / name
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _env(bindir: Path) -> dict[str, str]:
    return {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}"}


def _cli(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )  # fmt: skip


SEED = {
    "python3": "3.9",
    "pytest": "9.1.1",
    "zig": "0.17.0",
    "shellcheck": "0.11.0",
    "cargo-machete": "0.7.0",
    "cargo-audit": "0.21.0",
    "cargo-zigbuild": "0.24.0",
    "cargo-llvm-cov": "0.6.24",
}


def test_the_floors_are_published_and_seeded_from_sys_updater() -> None:
    out = _cli(["--floors"])
    assert out.returncode == 0, out.stderr
    table = dict(line.split("\t") for line in out.stdout.splitlines() if line.strip())
    for tool, floor in SEED.items():
        assert table.get(tool) == floor, (tool, table)


def test_one_tool_has_one_floor() -> None:
    """A tool on several rows (python3 is on five) carries the same floor on each: two floors
    for one tool is a table that disagrees with itself."""
    seen: dict[str, str] = {}
    for line in (ROOT / "gates" / "required_tools.tsv").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        assert len(cols) == 6, cols
        tool, floor = cols[1], cols[5]
        assert seen.setdefault(tool, floor) == floor, (tool, seen[tool], floor)


@pytest.mark.parametrize(
    ("found", "ok"),
    [("0.16.0", False), ("0.17.0", True), ("0.18.1", True), ("1.0.0", True), ("0.9.9", False)],
)
def test_a_tool_below_its_floor_is_red_and_newer_is_green(
    tmp_path: Path, found: str, ok: bool
) -> None:
    """FLOOR, never pin: below is red and NAMES both versions; at or above -- any newer -- is
    green."""
    _fake(tmp_path / "bin", "zig", found)
    out = _cli(["--check", "--tool", "zig"], env=_env(tmp_path / "bin"))
    if ok:
        assert out.returncode == 0, (out.stdout, out.stderr)
    else:
        assert out.returncode == 1, (out.stdout, out.stderr)
        assert "zig" in out.stderr and found in out.stderr and "0.17.0" in out.stderr, out.stderr
        assert "floor" in out.stderr, out.stderr


def test_a_missing_tool_is_red_named_as_missing(tmp_path: Path) -> None:
    env = {**os.environ, "PATH": str(tmp_path)}
    out = subprocess.run(
        ["/bin/sh", "-c", f'"{sys.executable}" "{CLI}" --check --tool zig'],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
    )  # fmt: skip
    assert out.returncode == 1, (out.stdout, out.stderr)
    assert "zig" in out.stderr and "not installed" in out.stderr, out.stderr


def test_a_version_the_caller_read_is_judged_too() -> None:
    """`--found` hands the check a version the CALLER read -- the module through the gate's own
    runner -- so the interpreter judged is the one the gate runs."""
    assert _cli(["--check", "--tool", "pytest", "--found", "pytest 8.4.0"]).returncode == 1
    assert _cli(["--check", "--tool", "pytest", "--found", "pytest 9.2.0"]).returncode == 0


def test_a_tool_with_no_floor_is_judged_on_presence_alone(tmp_path: Path) -> None:
    _fake(tmp_path / "bin", "swiftlint", "0.0.1")
    out = _cli(["--check", "--tool", "swiftlint"], env=_env(tmp_path / "bin"))
    assert out.returncode == 0, (out.stdout, out.stderr)


def test_goh_require_refuses_a_tool_below_its_floor(tmp_path: Path) -> None:
    """The house's existing requirement check is where the floor bites: a gate that needs zig and
    finds 0.16.0 dies naming it, before any slow step runs."""
    _fake(tmp_path / "bin", "zig", "0.16.0")
    script = f'. "{COMMON}"; GOH_NAME=t; goh_require zig "brew install zig"; echo REACHED'
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env=_env(tmp_path / "bin"),
        check=False,
        cwd=tmp_path,
    )  # fmt: skip
    assert out.returncode != 0 and "REACHED" not in out.stdout, (out.stdout, out.stderr)
    assert "0.16.0" in out.stderr and "0.17.0" in out.stderr, out.stderr


def test_goh_require_passes_a_newer_tool(tmp_path: Path) -> None:
    _fake(tmp_path / "bin", "zig", "0.99.0")
    script = f'. "{COMMON}"; GOH_NAME=t; goh_require zig "brew install zig"; echo REACHED'
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        env=_env(tmp_path / "bin"),
        check=False,
        cwd=tmp_path,
    )  # fmt: skip
    assert out.returncode == 0 and "REACHED" in out.stdout, (out.stdout, out.stderr)
