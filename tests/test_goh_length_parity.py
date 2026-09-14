"""Length parity: `goh length` agrees with `check_file_length.py`.

Fixture git repos per case; asserts identical exit codes, identical ordered
`(path, lines)` over-cap lists, and identical measured-file counts in full
and staged modes. Either side's suffix set, exclusion, or line definition
drifting goes red.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "checks" / "check_file_length.py"
OVER_RE = re.compile(r"^\s*(\d+)\s+lines\s+(.+?)\s+\(\+\d+\)\s*$")
OK_RE = re.compile(r"OK — (\d+) file\(s\) within \d+ lines")


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


def _parse(out: str, err: str) -> tuple[list[tuple[str, int]], int | None]:
    over = [(m.group(2), int(m.group(1))) for m in (OVER_RE.match(l) for l in err.splitlines()) if m]
    checked = next((int(m.group(1)) for m in (OK_RE.search(l) for l in out.splitlines()) if m), None)
    return over, checked


def run_python(repo: Path, args: list[str]) -> tuple[int, list[tuple[str, int]], int | None]:
    r = subprocess.run(["python3", str(CHECKER), *args], cwd=repo, capture_output=True, text=True)
    over, checked = _parse(r.stdout, r.stderr)
    return r.returncode, over, checked


def run_goh(goh: Path, repo: Path, args: list[str]) -> tuple[int, list[tuple[str, int]], int | None]:
    r = subprocess.run([str(goh), "length", *args], cwd=repo, capture_output=True, text=True)
    over, checked = _parse(r.stdout, r.stderr)
    return r.returncode, over, checked


def lines(n: int, terminated: bool = True) -> bytes:
    body = b"x\n" * n
    return body if terminated else body.rstrip(b"\n")


FULL_CASES: dict[str, dict[str, bytes]] = {
    "exact": {"a.py": lines(100)},
    "over": {"a.py": lines(101)},
    "over_unterminated": {"a.py": lines(101, terminated=False)},
    "exact_unterminated": {"a.py": lines(100, terminated=False)},
    "docs_ignored": {"big.md": lines(5000)},
    "lock_ignored": {"_package.lock": lines(5000)},
    "empty": {"a.py": b""},
    "order": {"mid.py": lines(120), "big.py": lines(150), "small.py": lines(110)},
}


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, FULL_CASES[name])
    assert run_goh(goh, repo, ["--max", "100"]) == run_python(repo, ["--max", "100"])


def test_exclude_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"vendor/big.py": lines(200), "src/ok.py": lines(10)})
    args = ["--max", "100", "--exclude", "vendor/"]
    assert run_goh(goh, repo, args) == run_python(repo, args) == (0, [], 1)
    args = ["--max", "100"]
    assert run_goh(goh, repo, args) == run_python(repo, args)


def test_staged_sees_the_index(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": lines(10)})
    (repo / "a.py").write_bytes(lines(150))
    _git(repo, "add", "a.py")
    assert run_python(repo, ["--max", "100", "--staged"]) == run_goh(goh, repo, ["--max", "100", "--staged"])
    assert run_goh(goh, repo, ["--max", "100", "--staged"])[0] == 1


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": lines(10)})
    (repo / "a.py").write_bytes(lines(150))
    assert run_python(repo, ["--max", "100", "--staged"]) == run_goh(goh, repo, ["--max", "100", "--staged"]) == (0, [], 1)
