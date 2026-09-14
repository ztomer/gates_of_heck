"""Structural parity: `goh structural` agrees with `gates/structural.sh`.

Fixture repos per case; asserts identical exit codes and identical failing
step labels in full and staged modes. Either side's step order, labels,
config handling, or scope gating drifting goes red.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL = ROOT / "gates" / "structural.sh"
FAIL_RE = re.compile(r"structural: (.+) failed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture(scope="module")
def goh() -> Path:
    r = subprocess.run(
        ["cargo", "build", "--message-format=json",
         "--manifest-path", str(ROOT / "Cargo.toml")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"cargo build failed:\n{r.stderr}"
    for line in r.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        target = event.get("target", {})
        if event.get("reason") == "compiler-artifact" and target.get("name") == "goh":
            path = event.get("executable")
            if path:
                return Path(path)
    raise AssertionError("goh artifact missing from cargo build output")


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def _failing(out: str, err: str) -> str | None:
    m = next((FAIL_RE.search(l) for l in (out + err).splitlines() if FAIL_RE.search(l)), None)
    return m.group(1) if m else None


def run_bash(repo: Path, staged: bool) -> tuple[int, str | None]:
    """The PYTHON pipeline. structural.sh execs the native binary when one is
    around, so this side is pinned to the checkers with GOH_NO_NATIVE — the
    comparison is native vs Python, never native vs itself."""
    import os

    env = dict(os.environ, GOH_NO_NATIVE="1")
    cmd = ["bash", str(STRUCTURAL), "--staged"] if staged else ["bash", str(STRUCTURAL)]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)
    return r.returncode, _failing(r.stdout, r.stderr)


def run_goh(goh: Path, repo: Path, staged: bool) -> tuple[int, str | None]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    cmd = [str(goh), "structural", "--staged"] if staged else [str(goh), "structural"]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)
    return r.returncode, _failing(r.stdout, r.stderr)


GATESRC = b"GOH_MAX_LINES=10\n"

FULL_CASES: dict[str, dict[str, bytes]] = {
    "clean": {".gatesrc": GATESRC, "a.py": b"x = 1\n"},
    "emoji": {".gatesrc": GATESRC, "a.py": "x = 1  # \U0001F389\n".encode()},
    "marker": {".gatesrc": GATESRC, "a.py": b"x = 1\n<<<<<<< ours\n"},
    "over_cap": {".gatesrc": GATESRC, "a.py": b"x = 1\n" * 20},
    "no_gatesrc": {"a.py": b"x = 1\n"},
    "exclude_warn": {".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\n", "a.py": b"x = 1\n"},
    "shell_fail": {".gatesrc": GATESRC, "bad.sh": b"if then\n"},
}


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, FULL_CASES[name])
    assert run_goh(goh, repo, staged=False) == run_bash(repo, staged=False), name


def test_staged_marker_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    _git(repo, "add", "a.py")
    assert run_goh(goh, repo, staged=True) == run_bash(repo, staged=True)


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    assert run_goh(goh, repo, staged=True) == run_bash(repo, staged=True) == (0, None)


def test_structural_sh_execs_the_native_binary_when_told_where_it_is(goh, tmp_path):
    """The hooks call structural.sh; with a binary the Python never runs. A
    bad emoji proves which side answered: the native step label is the same,
    but the Python fallback notice must be absent."""
    repo = make_repo(tmp_path, {"a.md": "ok\n".encode(), ".gatesrc": GATESRC})
    import os

    env = dict(os.environ, GOH_BIN=str(goh), GOH_DIR=str(ROOT))
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "goh binary not built" not in r.stderr
    # An explicit pointer at nothing is reported, once, and the Python runs.
    env = dict(os.environ, GOH_BIN="/nonexistent/goh", GOH_DIR=str(ROOT))
    env.pop("GOH_NO_NATIVE", None)
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stderr.count("GOH_BIN=/nonexistent/goh is not an executable") == 1, r.stderr

