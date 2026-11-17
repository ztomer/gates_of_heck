"""A coverage part whose TESTS failed is starved, not broken (GOH.COVCASCADE).

The land gate of 2026-11-15 went red on two pytest tests run under the Rust coverage group (the
suite is that group's external part), and the step then closed on "coverage exports incomplete --
fix it, do not trust this run": a second red cause, a coverage fault, for a run whose only defect
was the two tests. A part that failed in the phase that RUNS the tests (the workspace's `cargo
test`, the external command) has no coverage to export because its input failed: the gate names
the tests (red) and says the measurement was starved by them (not red). A part that failed
anywhere else -- the build, the report -- is still an incomplete export
(tests/test_coverage_rust_exports.py pins that half).
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from conftest import hermetic_env, llvm_cov_shim
from cov_fakes import SHOW_ENV_FLOW
from test_coverage_gate import COV_GATE
from test_coverage_one_build import FAILING_TEST_CARGO
from timing_bounds import patience

# The workspace part passes (its run, its report); only the external command can fail.
PASSING_CARGO = (
    """\
#!/bin/bash
case "$1" in
  metadata)
    echo '{"packages":[{"name":"ft","manifest_path":"'"$PWD"'/Cargo.toml",
      "targets":[{"name":"ft","kind":["lib"]}]}]}'
    exit 0 ;;
  build)  # the external part's instrumented binaries, named the way cargo's artifact list does
    exe="$CARGO_LLVM_COV_TARGET_DIR/debug/ft"
    mkdir -p "${exe%/*}"; : >"$exe"; chmod +x "$exe"
    printf '{"reason":"compiler-artifact","filenames":[],"executable":"%s"}\\n' "$exe"
    exit 0 ;;
esac
"""
    + SHOW_ENV_FLOW
    + """\
case "$1 $2" in
  "llvm-cov report")
    out=""; prev=""
    for a in "$@"; do [ "$prev" = "--output-path" ] && out="$a"; prev="$a"; done
    printf 'SF:%s/src/lib.rs\\nDA:1,1\\nLF:1\\nLH:1\\nend_of_record\\n' "$PWD" >"$out"
    exit 0 ;;
esac
exit 0
"""
)

# What the house suite prints when two of its tests fail (the shape of the 2026-11-15 land).
PYTEST_RED = (
    "printf 'FAILED tests/test_env.py::test_no_env_dump - assert\\n"
    "FAILED tests/test_git.py::test_no_new_git_init_in_tests - assert\\n'; exit 1"
)


def _gate(tmp_path: Path, cargo: str, *extra: str) -> subprocess.CompletedProcess:
    bin_ = tmp_path / "fakebin"
    bin_.mkdir()
    shim = bin_ / "cargo"
    shim.write_text(cargo)
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    llvm_cov_shim(bin_)
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "lib.rs").write_text("pub fn f() {}\n")
    env = hermetic_env(PATH=f"{bin_}:/usr/bin:/bin")
    return subprocess.run(
        ["/bin/bash", str(COV_GATE), str(proj), "--lang", "rust", "--floor", "0", *extra],
        cwd=str(proj),
        capture_output=True,
        text=True,
        env=env,
        timeout=patience(120),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _red_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("✗")]


def _assert_one_red_cause(r: subprocess.CompletedProcess, test_name: str) -> None:
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "incomplete" not in out, f"a test failure read as an export fault:\n{out}"
    assert "starved" in out, f"the coverage verdict must say its input failed:\n{out}"
    red = _red_lines(r.stderr)
    assert len(red) == 1, f"one red cause, got {len(red)}:\n{out}"
    assert "TESTS FAILED" in red[0], red
    assert test_name in r.stderr, r.stderr


def test_a_failing_cargo_test_starves_coverage_with_one_red_cause(tmp_path: Path) -> None:
    r = _gate(tmp_path, FAILING_TEST_CARGO)
    _assert_one_red_cause(r, "tests::parses_the_header")
    assert "tests::parses_the_header" in r.stderr, r.stderr


def test_a_failing_external_suite_starves_coverage_with_one_red_cause(tmp_path: Path) -> None:
    r = _gate(tmp_path, PASSING_CARGO, "--external", PYTEST_RED)
    _assert_one_red_cause(r, "test_no_env_dump")
    assert "tests/test_env.py::test_no_env_dump" in r.stderr, r.stderr
    assert "tests/test_git.py::test_no_new_git_init_in_tests" in r.stderr, r.stderr


def test_a_passing_external_suite_is_measured(tmp_path: Path) -> None:
    # The fixture's own calibration: with the suite green the same gate is green, so the two tests
    # above are red because of the suite, never because the fake cannot run an external part.
    r = _gate(tmp_path, PASSING_CARGO, "--external", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "starved" not in r.stdout + r.stderr
