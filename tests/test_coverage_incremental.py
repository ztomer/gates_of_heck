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
    or subprocess.run(
        ["cargo", "llvm-cov", "--version"], capture_output=True, stdin=subprocess.DEVNULL
    ).returncode,
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
        cwd=crate, capture_output=True, text=True, env=hermetic_env(), timeout=600, stdin=subprocess.DEVNULL,
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
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    assert _cov(crate).endswith("100.0% of coverable lines")

    (crate / "tests" / "t1.rs").write_text(CALLS_B)  # a() is no longer called by anything
    assert _cov(crate).endswith("66.67% of coverable lines"), "a previous run's profile counted"

    (crate / "tests" / "t1.rs").unlink()
    (crate / "Cargo.toml").write_text(manifest.replace("0.1.0", "0.2.0"))
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    assert _cov(crate).endswith("66.67% of coverable lines"), "a stale binary counted"

    (crate / "src" / "lib.rs").write_text(LIB.replace("pub fn a() -> u32 {\n    1\n}\n\n", ""))
    assert _cov(crate).endswith("100.0% of coverable lines"), "removed code was still measured"


def test_a_binary_of_another_build_never_counts(tmp_path: Path) -> None:
    """The build dir outlives a build (5.1), so a member whose metadata hash changed (a version
    bump, an edition, a dependency) leaves its old instrumented binaries beside the new ones, and
    cargo-llvm-cov exports from EVERY executable of a member's name it finds there. Those carry the
    old source: removed code reads uncovered, at line numbers the current file may not have
    (sys_updater, 2026-10-17: 96.44% read 92.32%, misses at config.rs:287-465 of a 290-line file).
    Bump and remove at once: the old binary holds `a`, the current tree does not."""
    crate = tmp_path / "stale"
    (crate / "src").mkdir(parents=True)
    manifest = '[package]\nname = "stale"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    (crate / "Cargo.toml").write_text(manifest)
    tests_a = "    #[test]\n    fn aa() {\n        assert_eq!(super::a(), 1);\n    }\n"
    (crate / "src" / "lib.rs").write_text(LIB.replace("mod t {\n", "mod t {\n" + tests_a))
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    assert _cov(crate).endswith("100.0% of coverable lines")

    (crate / "Cargo.toml").write_text(manifest.replace("0.1.0", "0.2.0"))
    (crate / "src" / "lib.rs").write_text(LIB.replace("pub fn a() -> u32 {\n    1\n}\n\n", ""))
    subprocess.run(
        ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
    )
    assert _cov(crate).endswith("100.0% of coverable lines"), "a stale build's binary counted"


def test_a_recreated_tree_reuses_its_dependencies_build(tmp_path: Path) -> None:
    """The push gate's shape: its worktree is deleted after every push and recreated at the same
    path for the next. The instrumented build lived inside it, so every push rebuilt every crate's
    dependencies instrumented (media_server, 2026-10-17: 28 crates, ~393 s of coverage CPU per
    push). Now the build dir is keyed on the path and kept outside the tree: a dependency outside
    the recreated tree (as a registry crate is) is not compiled again, and the report is the same."""
    (tmp_path / "base" / "src").mkdir(parents=True)
    (tmp_path / "base" / "Cargo.toml").write_text(
        '[package]\nname = "base"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (tmp_path / "base" / "src" / "lib.rs").write_text("pub fn one() -> u32 {\n    1\n}\n")
    cache = tmp_path / "cov-cache"

    def tree() -> Path:
        crate = tmp_path / "run" / "proj"
        (crate / "src").mkdir(parents=True)
        (crate / "Cargo.toml").write_text(
            '[package]\nname = "proj"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n\n'
            '[dependencies]\nbase = { path = "../../base" }\n'
        )
        (crate / "src" / "lib.rs").write_text(
            "pub fn two() -> u32 {\n    base::one() + 1\n}\n\n"
            "#[cfg(test)]\nmod t {\n    #[test]\n    fn two() {\n        assert_eq!(super::two(), 2);\n    }\n}\n"
        )
        subprocess.run(
            ["cargo", "generate-lockfile", "-q"], cwd=crate, check=True, stdin=subprocess.DEVNULL
        )
        return crate

    def cov(crate: Path) -> tuple[str, str]:
        r = subprocess.run(
            ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), "--lang", "rust",
             "--floor", "0", str(crate)],
            cwd=crate, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL,
            env=hermetic_env(GOH_COV_CACHE=str(cache)), check=False,
        )  # fmt: skip
        assert r.returncode == 0, r.stdout + r.stderr
        log = (crate / "target" / "llvm-cov" / "lcov-parts" / "part-workspace.info.log").read_text()
        line = next(ln for ln in r.stdout.splitlines() if "[coverage]" in ln)
        return line.split(" (floor")[0][-40:], log

    first, log = cov(tree())
    assert "Compiling base" in log, "the first build compiles the dependency: no baseline"
    shutil.rmtree(tmp_path / "run")  # the push gate removes its worktree
    second, log = cov(tree())
    assert "Compiling proj" in log, log  # the recreated tree is rebuilt, as it must be
    assert "Compiling base" not in log, f"the dependency was rebuilt for a recreated tree:\n{log}"
    assert second == first
