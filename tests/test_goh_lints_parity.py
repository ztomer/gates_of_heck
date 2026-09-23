"""Lints parity: `goh lints` agrees with `check_lints_optin.py`.

Fixture workspaces per case; asserts identical exit codes plus
identical stdout/stderr. A scope rule, message, or stream drifting on
either side goes red.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_lints_optin.py"

POLICY = (
    "[workspace]\nmembers = [{members}]\n\n"
    "[workspace.lints.clippy]\nall = \"warn\"\n"
)
INHERIT = '[package]\nname = "{name}"\n\n[lints]\nworkspace = true\n'
BARE = '[package]\nname = "{name}"\n'
OWN_TABLE = '[package]\nname = "{name}"\n\n[lints.clippy]\nall = "warn"\n'


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    _git(repo, "add", "-A")
    return repo


def run_py(repo: Path) -> tuple[int, str, str]:
    r = subprocess.run(["python3", str(CHECK)], cwd=repo, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def run_goh(goh: Path, repo: Path) -> tuple[int, str, str]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    r = subprocess.run([str(goh), "lints"], cwd=repo, capture_output=True, text=True, env=env)
    return r.returncode, r.stdout, r.stderr


CASES: dict[str, dict[str, str]] = {
    "clean": {
        "Cargo.toml": POLICY.format(members='"a", "b"'),
        "a/Cargo.toml": INHERIT.format(name="a"),
        "b/Cargo.toml": INHERIT.format(name="b"),
    },
    "missing_table": {
        "Cargo.toml": POLICY.format(members='"a", "b"'),
        "a/Cargo.toml": INHERIT.format(name="a"),
        "b/Cargo.toml": BARE.format(name="b"),
    },
    "own_table": {
        "Cargo.toml": POLICY.format(members='"a"'),
        "a/Cargo.toml": OWN_TABLE.format(name="a"),
    },
    "excluded": {
        "Cargo.toml": (
            "[workspace]\nmembers = [\"a\", \"v\"]\nexclude = [\"v\"]\n\n"
            "[workspace.lints.clippy]\nall = \"warn\"\n"
        ),
        "a/Cargo.toml": INHERIT.format(name="a"),
        "v/Cargo.toml": BARE.format(name="v"),
    },
    "no_policy": {
        "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
        "a/Cargo.toml": BARE.format(name="a"),
    },
    "no_rust": {"README.md": "nothing\n"},
    "target_skipped": {
        "Cargo.toml": POLICY.format(members='"a"'),
        "a/Cargo.toml": INHERIT.format(name="a"),
        "target/x/Cargo.toml": POLICY.format(members=""),
    },
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_lints_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, CASES[name])
    assert run_goh(goh, repo) == run_py(repo), name
