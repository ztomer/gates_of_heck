"""The Rust coverage gate judges the change's lines on a REAL instrumented run (GOH.DIFFCOV).

The calibration, planted: a crate at 100% of a floor of 0 takes a new function its tests call
(green) and then one nobody calls (red, naming src/lib.rs and the function's lines), the floor
green both times -- the hole this closes is exactly that the floor alone passed it. One crate,
two coverage runs (the second incremental). tests/test_diff_cov.py covers the reader's rules.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, commit_all, git, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode,
    reason="cargo llvm-cov not available",
)

LIB = "pub fn a() -> u32 {\n    1\n}\n"
TESTS = "#[test]\nfn aa() {\n    assert_eq!(dc::a(), 1);\n}\n"
COVERED = "\npub fn b(x: u32) -> u32 {\n    x + 1\n}\n"
CALLS_B = "\n#[test]\nfn bb() {\n    assert_eq!(dc::b(1), 2);\n}\n"
UNCOVERED = "\npub fn c(x: u32) -> u32 {\n    x * 3\n}\n"


def _cov(crate: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust", "--floor",
         "0", str(crate)],
        cwd=crate, capture_output=True, text=True, env=hermetic_env(), timeout=patience(300),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip


def test_an_uncovered_added_line_is_red_and_a_covered_one_green(tmp_path: Path) -> None:
    crate = tmp_path / "dc"
    (crate / "src").mkdir(parents=True)
    (crate / "tests").mkdir()
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "dc"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (crate / ".gitignore").write_text("target/\n")
    (crate / "src" / "lib.rs").write_text(LIB)
    (crate / "tests" / "t.rs").write_text(TESTS)
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    fast_init(crate, "main")
    commit_all(crate, "base")
    git(crate, "checkout", "-q", "-b", "lane")

    (crate / "src" / "lib.rs").write_text(LIB + COVERED)
    (crate / "tests" / "t.rs").write_text(TESTS + CALLS_B)
    commit_all(crate, "b, tested")
    r = _cov(crate)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "[diff-coverage] 3/3 changed executable lines covered" in out, out

    (crate / "src" / "lib.rs").write_text(LIB + COVERED + UNCOVERED)
    commit_all(crate, "c, untested")
    r = _cov(crate)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "of coverable lines (floor 0%" in out, out  # the floor alone passes it
    assert "3 of 6 changed" in out, out
    assert all(f"src/lib.rs:{n}" in out for n in (9, 10, 11)), out  # c: signature, body, close
