"""coverage_gate.sh — argument handling, floor resolution, syntax, and one
REAL end-to-end rust run (skipped when cargo/cargo-llvm-cov are absent).

The gate is built against fixture mini-projects: the unit tests here pin the
contract WITHOUT invoking any coverage toolchain (a coverage build is far too
slow for a unit suite); the single e2e below proves the rust pipeline actually
runs and actually bites.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

COV_GATE = REPO_ROOT / "gates" / "coverage_gate.sh"
LOCAL_CI = REPO_ROOT / "gates" / "local_ci.sh"

ALLOWED_GLYPHS_RE = re.compile(r"[→·✓✗⚠↔↑↓←⌘⌥⌨⇧⌃⏎⎋↵⇒⇄]")


def run_gate(cwd: Path, *args: str, env_extra: dict | None = None):
    env = dict(os.environ)
    env.pop("GOH_COV_FLOOR_RUST", None)
    env.pop("GOH_COV_FLOOR_SWIFT", None)
    env.pop("GOH_COV_FLOOR_CPP", None)
    env.pop("GOH_COV_FLOOR_PY", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["/bin/bash", str(COV_GATE), *args],
        cwd=cwd, capture_output=True, text=True, env=env,
    )


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    d.mkdir()
    return d


# ── syntax-level correctness (shellcheck stand-in for CI without shellcheck) ──


def test_new_gate_scripts_pass_bash_n():
    for script in (COV_GATE, LOCAL_CI):
        r = subprocess.run(
            ["/bin/bash", "-n", str(script)], capture_output=True, text=True
        )
        assert r.returncode == 0, f"{script}: {r.stderr}"


def test_scripts_stay_under_the_500_line_cap():
    for script in (COV_GATE, LOCAL_CI):
        n = len(script.read_text().splitlines())
        assert n <= 500, f"{script} is {n} lines (cap 500)"


def test_scripts_use_only_kare_glyphs():
    # No decorative emoji may enter the gate sources themselves.
    for script in (COV_GATE, LOCAL_CI):
        text = script.read_text()
        stripped = ALLOWED_GLYPHS_RE.sub("", text)
        for ch in stripped:
            assert ord(ch) < 0x1F000 or ch in "→·✓✗⚠↔↑↓←⌘⌥⌨", (
                f"{script.name} contains non-Kare pictograph U+{ord(ch):04X} {ch!r}"
            )


# ── floor resolution: --floor > GOH_COV_FLOOR_<LANG> > named failure ──


def test_missing_floor_fails_naming_both_seams(proj):
    r = run_gate(proj, "--lang", "rust")
    assert r.returncode == 2
    combined = r.stdout + r.stderr
    assert "--floor" in combined
    assert "GOH_COV_FLOOR_RUST" in combined


def test_env_floor_gets_past_floor_stage(proj):
    # With GOH_COV_FLOOR_* set, the gate must move PAST floor resolution and
    # die later on the (absent) toolchain — proving the env seam was honored.
    r = run_gate(proj, "--lang", "rust", env_extra={"GOH_COV_FLOOR_RUST": "90"})
    assert r.returncode != 0
    assert "GOH_COV_FLOOR_RUST" not in (r.stdout + r.stderr)


def test_explicit_floor_beats_missing_env(proj):
    r = run_gate(proj, "--lang", "swift", "--floor", "80")
    assert r.returncode != 0
    assert "no coverage floor" not in (r.stdout + r.stderr).lower()


def test_non_numeric_flag_floor_rejected(proj):
    r = run_gate(proj, "--lang", "py", "--floor", "ninety")
    assert r.returncode == 2
    assert "ninety" in (r.stdout + r.stderr)


def test_non_numeric_env_floor_rejected_naming_var(proj):
    r = run_gate(proj, "--lang", "cpp", env_extra={"GOH_COV_FLOOR_CPP": "abc"})
    assert r.returncode == 2
    assert "GOH_COV_FLOOR_CPP" in (r.stdout + r.stderr)


# ── argument validation ──


def test_unknown_lang_rejected_listing_valid_ones(proj):
    r = run_gate(proj, "--lang", "fortran")
    assert r.returncode == 2
    for lang in ("rust", "swift", "cpp", "py"):
        assert lang in (r.stdout + r.stderr)


def test_lang_is_required(proj):
    r = run_gate(proj, "--floor", "90")
    assert r.returncode == 2


def test_nonexistent_path_rejected_naming_it(proj):
    r = run_gate(proj, "--lang", "rust", "--floor", "90", str(proj / "nope"))
    assert r.returncode == 2
    assert "nope" in (r.stdout + r.stderr)


def test_help_exits_zero_and_shows_usage(proj):
    r = run_gate(proj, "--help")
    assert r.returncode == 0
    assert "--lang" in r.stdout
    assert "--floor" in r.stdout


def test_unknown_flag_rejected(proj):
    r = run_gate(proj, "--frobnicate")
    assert r.returncode == 2


def test_ignore_requires_value(proj):
    r = run_gate(proj, "--lang", "rust", "--ignore")
    assert r.returncode == 2


# ── real end-to-end rust run (the pipeline must actually bite) ──

CARGO_TOML = '[package]\nname = "covfix"\nversion = "0.1.0"\nedition = "2021"\n'
LIB_FULLY_COVERED = (
    "pub fn add(a: u64, b: u64) -> u64 {\n"
    "    a + b\n"
    "}\n"
)
LIB_WITH_GAP = (
    LIB_FULLY_COVERED
    + "\n"
    + "pub fn unused(a: u64) -> u64 {\n"
    + "    a * 3\n"
    + "}\n"
)
TEST_ALL = (
    "#[cfg(test)]\n"
    "mod tests {\n"
    "    #[test]\n"
    "    fn adds() { assert_eq!(covfix::add(1, 2), 3); }\n"
    "}\n"
)


def _mk_crate(root: Path, lib: str) -> Path:
    c = root / "covfix"
    (c / "src").mkdir(parents=True)
    (c / "Cargo.toml").write_text(CARGO_TOML)
    (c / "src" / "lib.rs").write_text(lib)
    (c / "src" / "lib.rs").with_suffix(".rs")
    (c / "tests").mkdir()
    (c / "tests" / "all.rs").write_text(TEST_ALL)
    return c


def _llvm_cov_available() -> bool:
    if shutil.which("cargo") is None:
        return False
    return (
        subprocess.run(
            ["cargo", "llvm-cov", "--version"], capture_output=True
        ).returncode
        == 0
    )


@pytest.mark.skipif(not _llvm_cov_available(), reason="cargo llvm-cov not available")
def test_rust_end_to_end_green_then_red(tmp_path):
    crate = _mk_crate(tmp_path, LIB_FULLY_COVERED)
    r = run_gate(crate, "--lang", "rust", "--floor", "100")
    assert r.returncode == 0, r.stdout + r.stderr

    gapped = _mk_crate(tmp_path / "gap", LIB_WITH_GAP)
    r2 = run_gate(gapped, "--lang", "rust", "--floor", "100")
    assert r2.returncode == 1
    # Exact uncovered-line reporting is the contract: the gate must say WHICH
    # lines, not just that a percentage slipped. unused() occupies lines 5-7.
    combined = r2.stdout + r2.stderr
    assert "/src/lib.rs:" in combined
    assert "5" in combined and "6" in combined and "7" in combined
