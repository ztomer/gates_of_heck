"""Markers parity: `goh markers` agrees with `check_no_conflict_markers.py`.

Builds small fixture git repos per case and asserts identical exit codes and
identical `(path, line)` violation sets in full and staged modes. If either
side's marker definition drifts, this goes red.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "checks" / "check_no_conflict_markers.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture(scope="module")
def goh() -> Path:
    # Shared CARGO_TARGET_DIR: resolve the binary from cargo's JSON stream.
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
        (repo / name).write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def run_python(repo: Path, staged: bool) -> tuple[int, set[tuple[str, int]]]:
    cmd = ["python3", str(CHECKER), "--staged"] if staged else ["python3", str(CHECKER)]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    return r.returncode, _violations(r.stderr)


def run_goh(goh: Path, repo: Path, staged: bool) -> tuple[int, set[tuple[str, int]]]:
    cmd = [str(goh), "markers", "--staged"] if staged else [str(goh), "markers"]
    r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    return r.returncode, _violations(r.stderr)


def _violations(stderr: str) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for line in stderr.splitlines():
        line = line.strip()
        if ":" not in line or line.startswith(("→", "✗")):
            continue
        path, _, rest = line.partition(":")
        lineno, _, _ = rest.partition(":")
        if lineno.strip().isdigit():
            out.add((path.strip(), int(lineno.strip())))
    return out


FULL_CASES: dict[str, bytes] = {
    "clean": b"hello\nworld\n",
    "ours": b"clean\n<<<<<<< ours\nmiddle\n",
    "theirs": b"a\n>>>>>>> theirs\n",
    "base": b"x\n||||||| base\n",
    "bare_eof": b"a\n<<<<<<<",
    "underline": b"title\n=======\nbody\n",
    "midline": b"echo foo >>>>>>> bar\n",
    "glued": b"<<<<<<<glued\n",
    "short": b"<<<<<< six\n",
    "binary": b"\x00\x01\x02\n<<<<<<< ours\n",
}


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, {"f.txt": FULL_CASES[name]})
    py_code, py_hits = run_python(repo, staged=False)
    rs_code, rs_hits = run_goh(goh, repo, staged=False)
    assert rs_code == py_code, f"{name}: goh={rs_code} python={py_code}"
    assert rs_hits == py_hits, f"{name}: goh={rs_hits} python={py_hits}"


def test_staged_sees_the_index(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(b"clean\n<<<<<<< ours\n")
    _git(repo, "add", "f.txt")
    assert run_python(repo, staged=True) == run_goh(goh, repo, staged=True)
    assert run_goh(goh, repo, staged=True)[0] == 1


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(b"clean\n<<<<<<< ours\n")
    py_code, py_hits = run_python(repo, staged=True)
    rs_code, rs_hits = run_goh(goh, repo, staged=True)
    assert (rs_code, rs_hits) == (py_code, py_hits) == (0, set())
    # ...while full mode polices the worktree on both sides.
    assert run_python(repo, staged=False)[0] == run_goh(goh, repo, staged=False)[0] == 1
