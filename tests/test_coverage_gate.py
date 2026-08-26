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


# ── swift mode: engine flag + cov:ignore region forgiveness ──────────────────
#
# The pipeline is proven against a FAKE toolchain on PATH: `swift` and
# `xcrun` shims that emit canned llvm-cov payloads. That keeps the unit suite
# fast while still exercising the real bash gate → python helper → report
# processing chain end to end (calibration: the canned payloads are modeled
# on REAL output captured from xcodebuild/llvm-cov on this machine).

import importlib.util
import json
import stat
import sys

SWIFT_HELPER = REPO_ROOT / "gates" / "coverage_swift.py"


def _load_swift_helper():
    spec = importlib.util.spec_from_file_location("coverage_swift", SWIFT_HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_engine_flag_rejects_unknown_value(proj):
    r = run_gate(proj, "--lang", "swift", "--floor", "80",
                 "--engine", "bazel", env_extra={"GOH_COV_FLOOR_SWIFT": "80"})
    assert r.returncode == 2
    assert "engine" in (r.stdout + r.stderr)


def test_engine_env_var_rejected_when_bogus(proj):
    r = run_gate(proj, "--lang", "swift", "--floor", "80",
                 env_extra={"GOH_COV_FLOOR_SWIFT": "80",
                            "GOH_COV_SWIFT_ENGINE": "xcpretty"})
    assert r.returncode == 2
    assert "GOH_COV_SWIFT_ENGINE" in (r.stdout + r.stderr)


def test_parse_markers_requires_reason():
    cov = _load_swift_helper()
    excluded, errors = cov.parse_markers([
        "let a = 1",
        "// cov:ignore: display geometry is not testable headlessly",
        "let b = NSScreen.main",
        "// cov:ignore-start",
        "let c = 3",
        "// cov:ignore-end",
    ])
    assert excluded == {2, 4, 5, 6}
    assert len(errors) == 1 and errors[0].startswith("4:") and "reason" in errors[0]


def test_parse_markers_unclosed_block_is_an_error():
    cov = _load_swift_helper()
    excluded, errors = cov.parse_markers([
        "// cov:ignore-start: why",
        "let a = 1",
    ])
    assert excluded == {1, 2}
    assert any("unclosed" in e for e in errors)


def test_adjust_coverage_forgives_only_uncovered_marker_lines():
    cov = _load_swift_helper()
    counts = {1: 1, 2: 0, 3: 1, 4: 1}
    pct, forgiven = cov.adjust_coverage(
        raw_total=4, raw_covered=3, counts=counts, excluded={2})
    # line 2 is uncovered AND marked → removed from the denominator only.
    assert forgiven == 1
    assert abs(pct - 100.0) < 1e-9


def test_adjust_coverage_ignores_marked_but_covered_lines():
    cov = _load_swift_helper()
    counts = {1: 5}
    pct, forgiven = cov.adjust_coverage(1, 1, counts, excluded={1})
    assert forgiven == 0 and abs(pct - 100.0) < 1e-9


def test_xccov_report_parse_matches_live_format():
    # Modeled byte-for-byte on `xcrun xccov view --report` captured from a
    # live xcresult on this machine (probe 2026-08-25).
    cov = _load_swift_helper()
    report = (
        "Name                                    Coverage      \n"
        "---------------------------------- ------------- \n"
        "PkgTests                               50.00% (4/8)  \n"
        "    /tmp/p/Sources/cov/lib.swift      20.00% (1/5)  \n"
        "        covered()                    100.00% (1/1) \n"
        "    /tmp/p/Tests/T.swift            100.00% (3/3)  \n"
    )
    files = cov.parse_xccov_report(report)
    assert files == {
        "/tmp/p/Sources/cov/lib.swift": (1, 5),
        "/tmp/p/Tests/T.swift": (3, 3),
    }
    scoped = cov.parse_xccov_report(report, ignore_re=r"/Tests/")
    assert list(scoped) == ["/tmp/p/Sources/cov/lib.swift"]


# ---- fake-toolchain end-to-end through the real bash gate --------------------
#
# The toolchain builder (swift/xcrun/xcodebuild shims + canned payloads) lives
# in conftest.py, shared with tests/test_coverage_swift_selection.py.

from conftest import mk_fake_swift_toolchain



PROJ_DIR = lambda root: root / "proj"


def _run_with_path(bin_dir: Path, *args: str, env_extra: dict | None = None):
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    for k in ("GOH_COV_FLOOR_SWIFT", "GOH_COV_SWIFT_ENGINE", "GOH_COV_XCRESULT"):
        env.pop(k, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["/bin/bash", str(COV_GATE), *args],
        cwd=str(bin_dir), capture_output=True, text=True, env=env,
    )


def test_spm_engine_below_floor_reports_raw_pct(tmp_path):
    bin_ = mk_fake_swift_toolchain(tmp_path)
    r = _run_with_path(bin_, str(tmp_path / "proj"), "--lang", "swift", "--floor", "90")
    assert r.returncode == 1
    assert "50.0%" in r.stdout + r.stderr or "50.00%" in r.stdout + r.stderr


def test_cov_ignore_markers_lift_coverage_over_the_floor(tmp_path):
    bin_ = mk_fake_swift_toolchain(tmp_path)
    lib = tmp_path / "proj" / "Sources" / "pkg" / "lib.swift"
    lines = lib.read_text().splitlines()
    lines[5] = "line 6 // cov:ignore-start: hardware path, untestable headlessly"
    lines[9] = "line 10 // cov:ignore-end"
    lib.write_text("\n".join(lines) + "\n")
    r = _run_with_path(bin_, str(tmp_path / "proj"), "--lang", "swift", "--floor", "90")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "forgave" in (r.stdout + r.stderr).lower()


def test_marker_without_reason_is_a_gate_error_not_silent_pass(tmp_path):
    bin_ = mk_fake_swift_toolchain(tmp_path)
    lib = tmp_path / "proj" / "Sources" / "pkg" / "lib.swift"
    lib.write_text(lib.read_text() + "// cov:ignore\n")
    r = _run_with_path(bin_, str(tmp_path / "proj"), "--lang", "swift", "--floor", "10")
    assert r.returncode == 2
    assert "reason" in r.stderr


def test_xcodebuild_engine_runs_and_applies_floor(tmp_path):
    bin_ = mk_fake_swift_toolchain(tmp_path)
    env_extra = {"GOH_COV_FLOOR_SWIFT": "40", "GOH_COV_SCHEME": "App"}
    r = _run_with_path(bin_, str(tmp_path / "proj"), "--lang", "swift", "--floor", "40",
                       env_extra=env_extra)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "50.0%" in r.stdout + r.stderr or "50.00%" in r.stdout + r.stderr


def test_xcodebuild_missing_is_a_named_precondition(tmp_path):
    bare = tmp_path / "nobin"
    bare.mkdir()
    # A PATH with NO xcodebuild anywhere: the helper must name it, exit 2.
    env = dict(os.environ)
    env["PATH"] = "/no/such/bin"
    r = subprocess.run(
        [sys.executable, str(SWIFT_HELPER), "--engine", "xcodebuild",
         "--floor", "80", "--proj", str(tmp_path)],
        capture_output=True, text=True, env=env,
    )
    assert r.returncode == 2
    assert "xcodebuild" in r.stderr
