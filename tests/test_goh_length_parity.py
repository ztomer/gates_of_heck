"""Length parity: `goh length` agrees with `check_file_length.py`.

Fixture git repos per case; asserts identical exit codes, identical ordered
`(path, lines)` over-cap lists, and identical measured-file counts in full
and staged modes. Either side's suffix set, exclusion, or line definition
drifting goes red.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from reference_kit import load_reference, reference_path  # noqa: E402
from _fast_git import fast_init  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHECKER = reference_path("checks/check_file_length.py")
OVER_RE = re.compile(r"^\s*(\d+)\s+lines\s+(.+?)\s+\(\+\d+\)\s*$")
OK_RE = re.compile(r"OK — (\d+) file\(s\) within \d+ lines")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    fast_init(repo)
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def _parse(out: str, err: str) -> tuple[list[tuple[str, int]], int | None]:
    over = [
        (m.group(2), int(m.group(1))) for m in (OVER_RE.match(l) for l in err.splitlines()) if m
    ]
    checked = next(
        (int(m.group(1)) for m in (OK_RE.search(l) for l in out.splitlines()) if m), None
    )
    return over, checked


def run_python(repo: Path, args: list[str]) -> tuple[int, list[tuple[str, int]], int | None]:
    r = subprocess.run(["python3", str(CHECKER), *args], cwd=repo, capture_output=True, text=True)
    over, checked = _parse(r.stdout, r.stderr)
    return r.returncode, over, checked


def run_goh(
    goh: Path, repo: Path, args: list[str]
) -> tuple[int, list[tuple[str, int]], int | None]:
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
    assert run_python(repo, ["--max", "100", "--staged"]) == run_goh(
        goh, repo, ["--max", "100", "--staged"]
    )
    assert run_goh(goh, repo, ["--max", "100", "--staged"])[0] == 1


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"a.py": lines(10)})
    (repo / "a.py").write_bytes(lines(150))
    assert (
        run_python(repo, ["--max", "100", "--staged"])
        == run_goh(goh, repo, ["--max", "100", "--staged"])
        == (0, [], 1)
    )


# Build scripts are source. ZoneWM, 2026-10-05: a 698-line `Makefile` passed the 500-line cap
# because the scope was a SUFFIX list and a Makefile has no suffix; `mk/*.mk` was invisible too.
BUILD_FILES = {
    "Makefile": lines(101),
    "sub/GNUmakefile": lines(101),
    "lower/makefile": lines(101),
    "mk/rules.mk": lines(101),
    "CMakeLists.txt": lines(101),
    "cmake/find.cmake": lines(101),
    "justfile": lines(101),
    "Justfile.d/Justfile": lines(101),
    # Controls: a generated automake template, and a name that merely CONTAINS one of the above.
    "Makefile.in": lines(500),
    "docs/Makefile-notes.txt": lines(500),
}


def test_build_scripts_are_capped_in_both_tiers(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, BUILD_FILES)
    want = sorted(p for p, body in BUILD_FILES.items() if body == lines(101))
    py = run_python(repo, ["--max", "100"])
    assert py[0] == 1 and sorted(p for p, _ in py[1]) == want, py
    assert run_goh(goh, repo, ["--max", "100"]) == py


def _rust_strings(name: str) -> list[str]:
    src = (ROOT / "crates" / "goh" / "src" / "length.rs").read_text(encoding="utf-8")
    body = re.search(rf"pub const {name}: &\[&str\] = &\[(.*?)\];", src, re.S)
    assert body, f"length.rs no longer declares {name}"
    return re.findall(r'"([^"]*)"', body.group(1))


def test_the_two_tiers_scope_the_cap_from_one_list() -> None:
    """The suffix list was written twice and "mirrored" by a comment. A suffix added to one tier is
    a file one tier caps and the other waves through; this compares the lists, not a sample."""
    mod = load_reference("check_file_length")
    assert list(mod.SOURCE_SUFFIXES) == _rust_strings("SOURCE_SUFFIXES")
    assert list(mod.SOURCE_NAMES) == _rust_strings("SOURCE_NAMES")
