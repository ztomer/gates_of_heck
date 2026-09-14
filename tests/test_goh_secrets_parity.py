"""Secrets parity: `goh secrets` agrees with `check_no_secrets.py`.

Fixture git repos per case; asserts identical exit codes, identical ordered
`(path, line, col, kind)` finding lists, and identical clean-file counts in
full and staged modes. Either side's patterns, suppression, or columns
drifting goes red.

NOTE: tails are built with repetition, never written as literals — a
matchable tail in this source would trip the very gate under test.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "checks" / "check_no_secrets.py"
HIT_RE = re.compile(r"^\s*(.+?):(\d+):(\d+): (.+)$")
OK_RE = re.compile(r"OK — (\d+) (staged|tracked) files clean")

GHP = "ghp_" + "A" * 36
GHO = "gho_" + "B" * 36
PAT = "github_pat_" + "C" * 22
SK_ANT = "sk-ant-" + "d" * 20
SK_PROJ = "sk-proj-" + "e" * 20
SK = "sk-" + "f" * 20
XOXB = "xoxb-" + "g" * 10
AKIA = "AKIA" + "H" * 16
SHORT = "ghp_" + "A" * 10


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


def _parse(out: str) -> tuple[list[tuple[str, int, int, str]], int | None]:
    # NOTE: the secrets checker reports findings on stdout, like the emoji
    # checker (markers/length use stderr — each parity reads its own stream).
    hits = [
        (m.group(1), int(m.group(2)), int(m.group(3)), m.group(4))
        for m in (HIT_RE.match(l) for l in out.splitlines())
        if m
    ]
    checked = next(
        (int(m.group(1)) for m in (OK_RE.search(l) for l in out.splitlines()) if m),
        None,
    )
    return hits, checked


def run_python(repo: Path, args: list[str]) -> tuple[int, list, int | None]:
    r = subprocess.run(["python3", str(CHECKER), *args], cwd=repo, capture_output=True, text=True)
    hits, checked = _parse(r.stdout)
    return r.returncode, hits, checked


def run_goh(goh: Path, repo: Path, args: list[str]) -> tuple[int, list, int | None]:
    r = subprocess.run([str(goh), "secrets", *args], cwd=repo, capture_output=True, text=True)
    hits, checked = _parse(r.stdout)
    return r.returncode, hits, checked


FULL_CASES: dict[str, bytes] = {
    "clean": b"nothing here\n",
    "github": f"tok = {GHP}\n".encode(),
    "short_tail": f"tok = {SHORT}\n".encode(),
    "oauth": f"tok = {GHO}\n".encode(),
    "fine_grained": f"tok = {PAT}\n".encode(),
    "anthropic": f"key = {SK_ANT}\n".encode(),
    "openai_proj": f"key = {SK_PROJ}\n".encode(),
    "openai": f"key = {SK}\n".encode(),
    "slack": f"tok = {XOXB}\n".encode(),
    "aws": f"id = {AKIA}\n".encode(),
    "key_bare": b"-----BEGIN " + b"PRIVATE KEY-----\n",
    "key_rsa": b"-----BEGIN RSA " + b"PRIVATE KEY-----\n",
    "key_pub": b"-----BEGIN " + b"PUBLIC KEY-----\n",
    "suppressed_same": f'K = "{GHP}"  # secret-ok: revoked vector\n'.encode(),
    "suppressed_above": f"# secret-ok: rotated 2026-01-01\nK = \"{GHP}\"\n".encode(),
    "bare_marker": f'K = "{GHP}"  # secret-ok:\n'.encode(),
    "unicode_col": f"\u2192 {GHP}\n".encode(),
    "binary": b"\xff\xfe\x00\x01\x02more bytes",
}


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, {"f.txt": FULL_CASES[name]})
    assert run_goh(goh, repo, []) == run_python(repo, [])


def test_exclude_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"vendor/e.py": f"tok = {GHP}\n".encode(), "src/ok.py": b"ok\n"})
    assert run_goh(goh, repo, ["--exclude", "vendor/"]) == run_python(repo, ["--exclude", "vendor/"]) == (0, [], 1)
    assert run_goh(goh, repo, []) == run_python(repo, [])


def test_staged_sees_the_index(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(f"tok = {GHP}\n".encode())
    _git(repo, "add", "f.txt")
    assert run_python(repo, ["--staged"]) == run_goh(goh, repo, ["--staged"])
    assert run_goh(goh, repo, ["--staged"])[0] == 1


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(f"tok = {GHP}\n".encode())
    assert run_python(repo, ["--staged"]) == run_goh(goh, repo, ["--staged"]) == (0, [], 1)
