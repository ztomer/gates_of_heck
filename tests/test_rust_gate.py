"""rust_gate.sh — parity proof of the un-forked rewrite against the legacy
fork (tests/fixtures/rust_gate_legacy.sh), on REAL crates.

A2 (parity proof before cutover): old and new must agree on the exit-code
class for every scenario — clean, dirty fmt, clippy warning, #[allow]
present, and checker-absent. Output wording may differ (the new gate uses
the shared _common.sh contract); behavior may not.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

LEGACY = REPO_ROOT / "tests" / "fixtures" / "rust_gate_legacy.sh"
CURRENT = REPO_ROOT / "gates" / "rust_gate.sh"
CHECKER = REPO_ROOT / "checks" / "check_no_allow.py"

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None, reason="cargo not installed"
)

CARGO_TOML = '[package]\nname = "parity"\nversion = "0.1.0"\nedition = "2021"\n'
LIB_CLEAN = "pub fn add(a: u64, b: u64) -> u64 {\n    a + b\n}\n"
LIB_DIRTY = "pub fn add(a: u64,b: u64) -> u64 {\n    a + b  }\n"
LIB_WARN = "pub fn f() -> u64 { let x = 1u64; x }\n"          # unused-var-ish lint bait
LIB_ALLOW = "#[allow(dead_code)]\npub fn dead() {}\n"
LIB_TESTED = (
    'pub fn add(a: u64, b: u64) -> u64 {\n    a + b\n}\n'
    '\n'
    '#[cfg(test)]\n'
    'mod tests {\n'
    '    use super::*;\n'
    '\n'
    '    #[test]\n'
    '    fn adds() {\n'
    '        assert_eq!(add(1, 2), 3);\n'
    '    }\n'
    '}\n'
)
LIB_UNTESTED = "pub fn untested() -> u64 {\n    42\n}\n"  # pub: no dead-code lint, 0% coverage


def _git(crate: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=crate, capture_output=True, check=True)


def mkcrate(tmp: Path) -> Path:
    # A git repo, like every repo the gate runs on: the house no-#[allow]
    # checker scans TRACKED Rust files, so a bare directory is not a subject.
    (tmp / "src").mkdir(parents=True)
    (tmp / "Cargo.toml").write_text(CARGO_TOML)
    _git(tmp, "init", "-q")
    _git(tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
         "--allow-empty", "-m", "init")
    return tmp


def write_lib(c: Path, body: str) -> None:
    (c / "src" / "lib.rs").write_text(body)
    _git(c, "add", "-A")


def run_script(script: Path, crate: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(script), str(crate)],
        cwd=crate, capture_output=True, text=True,
        env={**os.environ, **(env or {})},
    )


@pytest.fixture(scope="module")
def warm_crate(tmp_path_factory):
    """One compiled crate so clippy's dependency build doesn't dominate."""
    c = mkcrate(tmp_path_factory.mktemp("warm"))
    write_lib(c, LIB_CLEAN)
    r = subprocess.run(["cargo", "build", "-q"], cwd=c, capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[:500]
    return c


def clone(warm: Path, dest: Path) -> Path:
    c = mkcrate(dest)
    shutil.rmtree(c / "src")
    shutil.copytree(warm / "src", c / "src")
    _git(c, "add", "-A")
    return c


def test_clean_crate_both_pass(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "clean")
    assert run_script(LEGACY, c).returncode == 0
    assert run_script(CURRENT, c).returncode == 0


def test_dirty_fmt_both_fail_and_show_output(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "fmt")
    write_lib(c, LIB_DIRTY)
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode != 0 and new.returncode != 0
    # Contract property 2 in BOTH: failing tool output is printed.
    assert "Diff in" in old.stderr or "Diff in" in old.stdout
    assert "Diff in" in new.stderr or "Diff in" in new.stdout


def test_clippy_warning_both_fail(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "clippy")
    write_lib(c, LIB_WARN)
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode != 0 and new.returncode != 0
    assert "warning" in (old.stderr + old.stdout).lower()
    assert "warning" in (new.stderr + new.stdout).lower()


def test_allow_attr_fails_when_checker_installed(tmp_path, warm_crate):
    # LEGACY needed a repo-local copy of the checker; CURRENT runs the house
    # one regardless.
    c = clone(warm_crate, tmp_path / "allow-old")
    write_lib(c, LIB_ALLOW)
    (c / "tools").mkdir()
    shutil.copy(CHECKER, c / "tools" / "check_no_allow.py")
    assert run_script(LEGACY, c).returncode != 0
    c = clone(warm_crate, tmp_path / "allow-new")
    write_lib(c, LIB_ALLOW)
    r = run_script(CURRENT, c)
    assert r.returncode != 0
    assert "no #[allow]" in r.stdout + r.stderr


def test_no_allow_is_never_skipped_for_want_of_a_local_copy(tmp_path, warm_crate):
    # LEGACY warned and skipped without tools/check_no_allow.py — which is how
    # every repo without a vendored copy went unchecked. CURRENT never skips.
    c = clone(warm_crate, tmp_path / "nochecker")
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode == 0 and new.returncode == 0
    assert "missing" in (old.stdout + old.stderr).lower()
    assert "skipped" not in (new.stdout + new.stderr).lower()
    assert "no #[allow]" in new.stdout + new.stderr


def test_cargo_subdir_argument_supported_by_both(tmp_path, warm_crate):
    # <repo> <cargo_dir> form: fmt/clippy run in the subdir, checker at root.
    # The git repo is the OUTER dir here (the real shape: one repo, a crate in
    # a subdir); the checker runs from the repo root and finds the crate.
    repo = tmp_path / "subdir-repo"
    repo.mkdir()
    inner = repo / "crates" / "parity"
    inner.mkdir(parents=True)
    (inner / "Cargo.toml").write_text(CARGO_TOML)
    shutil.copytree(warm_crate / "src", inner / "src")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    for script, expect_ok in ((LEGACY, True), (CURRENT, True)):
        r = subprocess.run(
            ["/bin/bash", str(script), str(repo), str(inner)],
            cwd=repo, capture_output=True, text=True,
        )
        assert (r.returncode == 0) is expect_ok, r.stderr


# ── coverage floor (current gate only; the legacy fork predates it) ──────────


def test_no_floor_warns_and_skips_coverage(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "nocovfloor")
    r = run_script(CURRENT, c)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no coverage floor" in (r.stdout + r.stderr).lower()


def test_floor_set_runs_coverage_green(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covgreen")
    write_lib(c, LIB_TESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=50\n")
    r = run_script(CURRENT, c)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "coverage" in (r.stdout + r.stderr).lower()


def test_floor_set_fails_when_uncovered(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covred")
    write_lib(c, LIB_UNTESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=80\n")
    r = run_script(CURRENT, c)
    assert r.returncode != 0, r.stdout + r.stderr
    combined = (r.stdout + r.stderr).lower()
    assert "coverage" in combined
    # The reason must be the floor, not a usage/config error: exit 2 is
    # argparse/exit-2 territory, and a miswired gate must not hide there.
    # (Rust mode reports "0.0% of coverable lines (floor 80%, 0/3 lines)".)
    assert "uncovered lines" in combined, r.stdout + r.stderr


# ── coverage deferred (GOH_RUST_COVERAGE=defer) ──────────────────────────────
# A commit hook gating every crate a release touches ran one cold instrumented
# build per crate (media_server, 2026-09-22: 27 crates, most of a 13-minute
# release) and the push gate ran them again. A repo whose push gate checks
# the floor defers it at commit time - by NAME, never by silence.


def test_deferred_coverage_is_named_and_not_run(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covdefer")
    write_lib(c, LIB_UNTESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=80\n")
    r = run_script(CURRENT, c, {"GOH_RUST_COVERAGE": "defer"})
    combined = r.stdout + r.stderr
    assert r.returncode == 0, "an uncovered crate passes only if coverage did not run: " + combined
    assert "coverage deferred" in combined.lower(), combined


def test_an_unknown_coverage_mode_fails(tmp_path, warm_crate):
    c = clone(warm_crate, tmp_path / "covmode")
    write_lib(c, LIB_TESTED)
    (c / ".gatesrc").write_text("GOH_COV_FLOOR_RUST=50\n")
    r = run_script(CURRENT, c, {"GOH_RUST_COVERAGE": "skip"})
    assert r.returncode != 0, r.stdout + r.stderr
    assert "GOH_RUST_COVERAGE" in r.stdout + r.stderr
