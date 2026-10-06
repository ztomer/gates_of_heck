"""Rust coverage builds incrementally, and its report is still a clean build's (roadmap 3.1).

`_coverage_rust.sh` ran `cargo llvm-cov clean --workspace` first, rebuilding every member crate
instrumented on every run (mediaops-rs: 20 s cleaned, 12 s not, identical report). The clean is now
only the PROFILES; the build is incremental. What must hold, each phase of ONE real crate here (one
cargo cost): a report equals the clean build's after a test stops calling a function (a previous
run's profile must not carry the call), after a test target is deleted and the version bumped (a
stale binary of another metadata hash must not count), and after covered code is removed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or subprocess.run(["cargo", "llvm-cov", "--version"], capture_output=True).returncode,
    reason="cargo llvm-cov not available",
)

LIB = (
    "pub fn a() -> u32 {\n    1\n}\n\npub fn b() -> u32 {\n    2\n}\n\n"
    "#[cfg(test)]\nmod t {\n    #[test]\n    fn bb() {\n        assert_eq!(super::b(), 2);\n    }\n}\n"
)
CALLS_A = "#[test]\nfn aa() {\n    assert_eq!(stale::a(), 1);\n}\n"
CALLS_B = "#[test]\nfn aa() {\n    assert_eq!(stale::b(), 2);\n}\n"


def _cov(crate: Path) -> str:
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust", "--floor",
         "0", str(crate)],
        cwd=crate, capture_output=True, text=True, env=hermetic_env(), timeout=600,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    return next(ln for ln in r.stdout.splitlines() if "[coverage]" in ln).split(" (floor")[0][-40:]


def test_an_incremental_report_is_the_clean_builds(tmp_path: Path) -> None:
    crate = tmp_path / "stale"
    (crate / "src").mkdir(parents=True)
    (crate / "tests").mkdir()
    manifest = '[package]\nname = "stale"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    (crate / "Cargo.toml").write_text(manifest)
    (crate / "src" / "lib.rs").write_text(LIB)
    (crate / "tests" / "t1.rs").write_text(CALLS_A)
    subprocess.run(["cargo", "generate-lockfile", "-q"], cwd=crate, check=True)
    assert _cov(crate).endswith("100.0% of coverable lines")

    (crate / "tests" / "t1.rs").write_text(CALLS_B)  # a() is no longer called by anything
    assert _cov(crate).endswith("66.67% of coverable lines"), "a previous run's profile counted"

    (crate / "tests" / "t1.rs").unlink()
    (crate / "Cargo.toml").write_text(manifest.replace("0.1.0", "0.2.0"))
    subprocess.run(["cargo", "generate-lockfile", "-q"], cwd=crate, check=True)
    assert _cov(crate).endswith("66.67% of coverable lines"), "a stale binary counted"

    (crate / "src" / "lib.rs").write_text(LIB.replace("pub fn a() -> u32 {\n    1\n}\n\n", ""))
    assert _cov(crate).endswith("100.0% of coverable lines"), "removed code was still measured"
