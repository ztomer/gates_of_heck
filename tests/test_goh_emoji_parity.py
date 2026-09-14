"""Emoji parity: `goh emoji` agrees with `check_no_emoji.py`.

Fixture git repos per case; asserts identical exit codes, identical
`(path, line, col, codepoint)` violation sets, and identical clean-file
counts in full and staged modes. Either side's allow-list, ranges, or
column counting drifting goes red.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "checks" / "check_no_emoji.py"
HIT_RE = re.compile(r"^\s*(.+?):(\d+):(\d+): U\+([0-9A-F]+)")
OK_RE = re.compile(r"OK — (\d+) (staged|tracked) files clean")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


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


def _parse(out: str, err: str) -> tuple[set[tuple[str, int, int, int]], int | None]:
    # NOTE: unlike the markers/length checkers, the emoji checker reports
    # violations on stdout — parse it, not stderr.
    del err
    hits = {
        (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4), 16))
        for m in (HIT_RE.match(l) for l in out.splitlines())
        if m
    }
    checked = next(
        (int(m.group(1)) for m in (OK_RE.search(l) for l in out.splitlines()) if m),
        None,
    )
    return hits, checked


def run_python(repo: Path, args: list[str]) -> tuple[int, set, int | None]:
    r = subprocess.run(["python3", str(CHECKER), *args], cwd=repo, capture_output=True, text=True)
    hits, checked = _parse(r.stdout, r.stderr)
    return r.returncode, hits, checked


def run_goh(goh: Path, repo: Path, args: list[str]) -> tuple[int, set, int | None]:
    r = subprocess.run([str(goh), "emoji", *args], cwd=repo, capture_output=True, text=True)
    hits, checked = _parse(r.stdout, r.stderr)
    return r.returncode, hits, checked


# NOTE: glyphs are built with escapes, never literals — a literal here would
# trip this very gate once staged (the house chr() convention).
PARTY = chr(0x1F389)
CHECK_BUTTON = chr(0x2705)
VS16 = chr(0xFE0F)
KEYCAP = chr(0x20E3)
BIG_ARROW = chr(0x2B05)
CIRCLED = chr(0x3297)
CURVED = chr(0x2934)
WAVY = chr(0x3030)

FULL_CASES: dict[str, bytes] = {
    "kare_clean": "→ ✓ ✗ ⚠ ↔ ↑ ↓ ← ⌘ ⇧ ⇒ © ® ™ ⇄ ↵ ⎋ ⌃ ⌥ ⌨\n".encode(),
    "party": f"hello {PARTY}\n".encode(),
    "check_button": f"{CHECK_BUTTON} done\n".encode(),
    "warn_vs16": f"⚠{VS16} ok\n".encode(),
    "keycap": f"call 1{VS16}{KEYCAP} now\n".encode(),
    "big_arrow": f"see {BIG_ARROW}\n".encode(),
    "circled": f"{CIRCLED} sale\n".encode(),
    "curved": f"turn {CURVED}\n".encode(),
    "wavy": f"sep {WAVY} end\n".encode(),
    "binary": b"\xff\xfe\x00\x01\x02more bytes",
    # 250 violations exercises the 200-hit display cap on both sides.
    "many": "".join(f"line {i} {PARTY}\n" for i in range(250)).encode(),
}


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, {"f.txt": FULL_CASES[name]})
    assert run_goh(goh, repo, []) == run_python(repo, [])


def test_allow_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": f"{PARTY} {CHECK_BUTTON}\n".encode()})
    assert run_goh(goh, repo, []) == run_python(repo, [])
    assert run_goh(goh, repo, [])[0] == 1
    for allow in (f"{PARTY} {CHECK_BUTTON}", f"{PARTY} {CHECK_BUTTON} "):
        assert run_goh(goh, repo, ["--allow", allow]) == run_python(repo, ["--allow", allow]) == (0, set(), 1)


def test_exclude_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"vendor/e.py": f"{PARTY}\n".encode(), "src/ok.py": "ok\n".encode()})
    assert run_goh(goh, repo, ["--exclude", "vendor/"]) == run_python(repo, ["--exclude", "vendor/"]) == (0, set(), 1)
    assert run_goh(goh, repo, []) == run_python(repo, [])


def test_staged_sees_the_index(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(f"clean {PARTY}\n".encode())
    _git(repo, "add", "f.txt")
    assert run_python(repo, ["--staged"]) == run_goh(goh, repo, ["--staged"])
    assert run_goh(goh, repo, ["--staged"])[0] == 1


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"f.txt": b"clean\n"})
    (repo / "f.txt").write_bytes(f"clean {PARTY}\n".encode())
    assert run_python(repo, ["--staged"]) == run_goh(goh, repo, ["--staged"]) == (0, set(), 1)
