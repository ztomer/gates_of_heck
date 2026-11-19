"""A tool below its FLOOR is a named failure, for every consumer (G2b, 2026-10-25).

`gates/required_tools.tsv` lists every tool a gate refuses without; its `floor` column says the
OLDEST version each may be. A floor, never a pin: the house runs the newest release of every layer
("why pin goh by version? The whole idea is to have ever improving qa gates", owner, 2026-10-25),
so a tool NEWER than its floor is green, always, and only a missing tool or one OLDER than its
floor is red. The floors were seeded from sys_updater's `tools/gate-floors.env` (R3.29), which
carried each one's reason; the reasons now sit above the rows in the manifest. python3's seed
(3.14) was once lowered to 3.9 on "evidence" that this suite's gates ran green on /usr/bin/python3
3.9.6 -- an accident of a reduced PATH, not a decision -- and is now 3.15, the house interpreter's
floor (G2d; tests/test_house_python.py).

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
from _tool_versions import floor as floor_of, pin as pin_of
from timing_bounds import patience

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


# The tools seeded from sys_updater (R3.29). Their VERSIONS live in the manifest only
# (GOH.CLASS.PARITY: tests/test_tool_version_manifest.py is red on a copy written here).
# pytest left the floors for a PIN (GOH.PYTESTPIN: a pytest release moves test ids).
SEED = (
    "python3",
    "zig",
    "shellcheck",
    "cargo-machete",
    "cargo-audit",
    "cargo-zigbuild",
    "cargo-llvm-cov",
)


def test_the_floors_are_published_and_seeded_from_sys_updater() -> None:
    out = _cli(["--floors"])
    assert out.returncode == 0, out.stderr
    table = dict(line.split("\t") for line in out.stdout.splitlines() if line.strip())
    raw = {}
    for line in (ROOT / "gates" / "required_tools.tsv").read_text().splitlines():
        cols = line.split("\t")
        if line.strip() and not line.startswith("#") and cols[5] != "-":
            raw.setdefault(cols[1], cols[5])
    for tool in SEED:
        assert table.get(tool) and table[tool] == raw.get(tool), (tool, table, raw)


def test_a_pinned_tool_is_published_at_its_pin() -> None:
    """`--floors` is a consumer contract: sys_updater's check_gate_floors.sh reads pytest's floor
    from it and refuses when the harness publishes none. pytest moving from a floor to a pin
    (GOH.PYTESTPIN) dropped it from `--floors`, and the release canary saw sys_updater turn red. A
    pin is the oldest release the house accepts, so it is published as the floor."""
    out = _cli(["--floors"])
    assert out.returncode == 0, out.stderr
    table = dict(line.split("\t") for line in out.stdout.splitlines() if line.strip())
    for tool in ("pytest", "ruff", "coverage"):
        assert table.get(tool) == pin_of(tool), (tool, table)


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


# zig's floor and versions around it, DERIVED from the manifest (GOH.CLASS.PARITY: a copy of the
# number here is a place a raise does not reach). `lex` is below the floor numerically and above it
# as text (0.9.9 against 0.17.0), the comparison trap.
ZIG = floor_of("zig")
_MAJ, _MIN, *_ = (int(x) for x in ZIG.split("."))
_BELOW = f"{_MAJ}.{_MIN - 1}.0" if _MIN else f"{_MAJ - 1}.99.0"
AROUND = {
    "below": _BELOW,
    "at": ZIG,
    "minor-up": f"{_MAJ}.{_MIN + 1}.1",
    "major-up": f"{_MAJ + 1}.0.0",
    "lex": f"{_MAJ}.{9 if _MIN >= 10 else max(_MIN - 1, 0)}.9" if _MIN else _BELOW,
}


@pytest.mark.parametrize(
    ("which", "ok"),
    [("below", False), ("at", True), ("minor-up", True), ("major-up", True), ("lex", False)],
)
def test_a_tool_below_its_floor_is_red_and_newer_is_green(
    tmp_path: Path, which: str, ok: bool
) -> None:
    """FLOOR, never pin: below is red and NAMES both versions; at or above -- any newer -- is
    green."""
    found = AROUND[which]
    _fake(tmp_path / "bin", "zig", found)
    out = _cli(["--check", "--tool", "zig"], env=_env(tmp_path / "bin"))
    if ok:
        assert out.returncode == 0, (out.stdout, out.stderr)
    else:
        assert out.returncode == 1, (out.stdout, out.stderr)
        assert "zig" in out.stderr and found in out.stderr and ZIG in out.stderr, out.stderr
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
    assert _cli(["--check", "--tool", "zig", "--found", f"zig {AROUND['below']}"]).returncode == 1
    assert (
        _cli(["--check", "--tool", "zig", "--found", f"zig {AROUND['minor-up']}"]).returncode == 0
    )


def test_a_tool_with_no_floor_is_judged_on_presence_alone(tmp_path: Path) -> None:
    _fake(tmp_path / "bin", "git-cliff", "0.0.1")
    out = _cli(["--check", "--tool", "git-cliff"], env=_env(tmp_path / "bin"))
    assert out.returncode == 0, (out.stdout, out.stderr)


def test_goh_require_refuses_a_tool_below_its_floor(tmp_path: Path) -> None:
    """The house's existing requirement check is where the floor bites: a gate that needs zig and
    finds one below its floor dies naming it, before any slow step runs."""
    _fake(tmp_path / "bin", "zig", AROUND["below"])
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
    assert AROUND["below"] in out.stderr and ZIG in out.stderr, out.stderr


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


# A PINNED tool's --check (GOH.PINCHECKFLOOR). GOH.PYTESTPIN made every pip row a pin and left
# `--check` judging a pinned tool's PRESENCE alone: a consumer whose box had pytest below the old
# 9.1.1 floor passed. The pin is the oldest release a consumer accepts (it is what `--floors`
# publishes), a newer one is green; in a goh checkout the pin is EXACT, the release CI installs
# (gates/required_pins.py, the same judgement tools/pytest.sh's `--pins-held` makes). The version
# seam is `--found`, the text the gate read through its own runner; versions come from the manifest.
PYTEST_PIN = pin_of("pytest")
_PMAJ, _PMIN, *_ = (int(x) for x in PYTEST_PIN.split("."))
PYTEST_OLD = f"{_PMAJ}.{_PMIN - 1}.0"  # below the pin: the old floor's release line
PYTEST_NEWER = f"{_PMAJ}.{_PMIN + 1}.0"


def _tree(root: Path, goh: bool) -> Path:
    """A consumer-shaped tree, or one shaped like a goh checkout (the toplevel marker
    gates/_self_gate.sh names: commit_gate.sh, _from_head.sh and crates/goh)."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, stdin=subprocess.DEVNULL)
    if goh:
        (root / "gates").mkdir()
        (root / "gates" / "commit_gate.sh").write_text("", encoding="utf-8")
        (root / "gates" / "_from_head.sh").write_text("", encoding="utf-8")
        (root / "crates" / "goh").mkdir(parents=True)
    return root


def _check(cwd: Path, found: str, tool: str = "pytest") -> subprocess.CompletedProcess[str]:
    out = subprocess.run(
        [sys.executable, str(CLI), "--check", "--tool", tool, "--found", found],
        capture_output=True,
        text=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        cwd=cwd,
        check=False,
    )  # fmt: skip
    return out


@pytest.mark.parametrize(
    ("found", "ok"),
    [(PYTEST_OLD, False), (PYTEST_PIN, True), (PYTEST_NEWER, True)],
)
def test_a_consumer_needs_at_least_a_pinned_tools_pin(tmp_path: Path, found: str, ok: bool) -> None:
    """The red-then-green of the item: an old pytest was green (the pin read as 'no floor')."""
    out = _check(_tree(tmp_path / "consumer", goh=False), f"pytest {found}")
    assert (out.returncode == 0) == ok, (out.stdout, out.stderr)
    if not ok:
        assert PYTEST_OLD in out.stderr and PYTEST_PIN in out.stderr, out.stderr


@pytest.mark.parametrize(
    ("found", "ok"),
    [(PYTEST_OLD, False), (PYTEST_PIN, True), (PYTEST_NEWER, False)],
)
def test_in_goh_a_pinned_tool_needs_exactly_its_pin(tmp_path: Path, found: str, ok: bool) -> None:
    out = _check(_tree(tmp_path / "goh", goh=True), f"pytest {found}")
    assert (out.returncode == 0) == ok, (out.stdout, out.stderr)
    if not ok:
        assert found in out.stderr and PYTEST_PIN in out.stderr, out.stderr


def test_the_real_checkout_is_a_goh_tree() -> None:
    """The marker is true of this very repo, so goh's own gates judge the exact pin."""
    assert _check(ROOT, f"pytest {PYTEST_NEWER}").returncode == 1
    assert _check(ROOT, f"pytest {PYTEST_PIN}").returncode == 0


def test_goh_require_floor_refuses_an_old_pinned_pytest_in_a_consumer(tmp_path: Path) -> None:
    """The gates' own entry (`goh_py_require pytest` -> goh_require_floor), not only the CLI."""
    root = _tree(tmp_path / "consumer", goh=False)
    script = (
        f'. "{COMMON}"; GOH_NAME=t; goh_require_floor pytest "pytest {PYTEST_OLD}"; echo REACHED'
    )
    out = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=patience(60),
        stdin=subprocess.DEVNULL,
        check=False,
        cwd=root,
    )  # fmt: skip
    assert out.returncode != 0 and "REACHED" not in out.stdout, (out.stdout, out.stderr)
    assert PYTEST_OLD in out.stderr and PYTEST_PIN in out.stderr, out.stderr


@pytest.mark.parametrize("tool", ["pytest-xdist", "numpy", "pillow", "coverage"])
def test_a_pinned_pip_tool_is_read_from_its_distribution_metadata(tool: str) -> None:
    """`python -m numpy --version` is no command: a pinned module with no CLI is read the way
    required_pins.py reads it, from the distribution's metadata in the gates' interpreter. The
    interpreter the suite runs holds the pins (tools/pytest.sh), so each is green on this box."""
    out = _cli(["--check", "--tool", tool])
    assert out.returncode == 0, (tool, out.stdout, out.stderr)
