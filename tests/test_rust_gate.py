"""rust_gate.sh — parity proof of the un-forked rewrite against the legacy
fork (tests/fixtures/rust_gate_legacy.sh), on REAL crates.

A2 (parity proof before cutover): old and new must agree on the exit-code
class for every scenario — clean, dirty fmt, clippy warning, #[allow]
present, and checker-absent. Output wording may differ (the new gate uses
the shared _common.sh contract); behavior may not.
"""

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


def mkcrate(tmp: Path) -> Path:
    (tmp / "src").mkdir(parents=True)
    (tmp / "Cargo.toml").write_text(CARGO_TOML)
    return tmp


def write_lib(c: Path, body: str) -> None:
    (c / "src" / "lib.rs").write_text(body)


def run_script(script: Path, crate: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(script), str(crate)],
        cwd=crate, capture_output=True, text=True,
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
    for name in ("allow-old", "allow-new"):
        c = clone(warm_crate, tmp_path / name)
        write_lib(c, LIB_ALLOW)
        tools = c / "tools"
        tools.mkdir()
        shutil.copy(CHECKER, tools / "check_no_allow.py")
        if name.endswith("-old"):
            assert run_script(LEGACY, c).returncode != 0
        else:
            assert run_script(CURRENT, c).returncode != 0


def test_missing_checker_warns_and_passes_in_both(tmp_path, warm_crate):
    # No tools/check_no_allow.py → both warn+skip; neither hard-fails.
    c = clone(warm_crate, tmp_path / "nochecker")
    old = run_script(LEGACY, c)
    new = run_script(CURRENT, c)
    assert old.returncode == 0 and new.returncode == 0
    assert "missing" in (old.stdout + old.stderr).lower()
    assert "skipped" in (new.stdout + new.stderr).lower()


def test_cargo_subdir_argument_supported_by_both(tmp_path, warm_crate):
    # <repo> <cargo_dir> form: fmt/clippy run in the subdir, checker at root.
    repo = tmp_path / "subdir-repo"
    repo.mkdir()
    inner = clone(warm_crate, repo / "crates" / "parity")
    for script, expect_ok in ((LEGACY, True), (CURRENT, True)):
        r = subprocess.run(
            ["/bin/bash", str(script), str(repo), str(inner)],
            cwd=repo, capture_output=True, text=True,
        )
        assert (r.returncode == 0) is expect_ok, r.stderr
