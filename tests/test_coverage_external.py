"""`coverage_gate.sh --lang rust --external CMD` counts a binary's runs from OUTSIDE cargo test.

gates_of_heck's native checks are tested by a pytest suite that drives the `goh` binary; the Rust
coverage gate measured only `cargo test`, and read 62.5% for code the suite covers (2026-10-06).
The seam builds the binaries instrumented, runs CMD with `$GOH_COVERAGE_BIN_DIR` naming them, and
counts the profiles as one more part. Pinned on a crate whose ONLY coverage is running its binary.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env


def run_gate(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """`coverage_gate.sh` in `cwd`, with no inherited GOH_* (an ambient floor or command)."""
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "coverage_gate.sh"), *args, str(cwd)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=hermetic_env(),
        timeout=600,
    )


pytestmark = [
    pytest.mark.skipif(
        shutil.which("cargo") is None
        or subprocess.run(["cargo", "llvm-cov", "--version"], capture_output=True).returncode,
        reason="cargo llvm-cov not available",
    ),
]

MAIN = (
    "fn seven() -> u32 {\n"
    "    7\n"
    "}\n"
    "\n"
    "fn main() {\n"
    '    if std::env::args().nth(1).as_deref() == Some("go") {\n'
    '        println!("{}", seven());\n'
    "    }\n"
    "}\n"
)


def _crate(root: Path) -> Path:
    c = root / "binonly"
    (c / "src").mkdir(parents=True)
    (c / "Cargo.toml").write_text(
        '[package]\nname = "binonly"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (c / "src" / "main.rs").write_text(MAIN)
    subprocess.run(["cargo", "generate-lockfile", "-q"], cwd=c, check=True)
    return c


def test_a_binary_covered_only_from_outside_is_counted(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    alone = run_gate(crate, "--lang", "rust", "--floor", "90")
    assert alone.returncode == 1, "cargo test alone should NOT cover a bin with no tests"
    seen = run_gate(
        crate, "--lang", "rust", "--floor", "90", "--external", '"$GOH_COVERAGE_BIN_DIR/binonly" go'
    )
    assert seen.returncode == 0, seen.stdout + seen.stderr


def test_a_failing_external_command_is_a_missing_part_not_a_smaller_report(tmp_path: Path) -> None:
    crate = _crate(tmp_path)
    r = run_gate(crate, "--lang", "rust", "--floor", "0", "--external", "exit 3")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "external: exit 3" in r.stdout + r.stderr, r.stdout + r.stderr


def test_external_is_a_rust_only_seam(tmp_path: Path) -> None:
    r = run_gate(tmp_path, "--lang", "py", "--floor", "0", "--external", "true")
    assert r.returncode == 2 and "rust-only" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_external_command_runs_in_the_callers_environment(tmp_path: Path) -> None:
    """CMD is a suite that builds crates of its own: the instrumented build's environment
    (`RUSTC_WRAPPER`, `CARGO_LLVM_COV*`, the coverage target dir) leaked into it made every cargo
    fixture inside the suite an instrumented build in the wrong target dir, and a nested coverage
    gate fail (2026-10-06, four suites red). CMD gets the caller's environment, plus the profile
    destination and `$GOH_COVERAGE_BIN_DIR`."""
    crate = _crate(tmp_path)
    seen = tmp_path / "seen.env"
    probe = f'env > "{seen}"; "$GOH_COVERAGE_BIN_DIR/binonly" go'
    env = hermetic_env(CARGO_TARGET_DIR=str(tmp_path / "callers-target"))
    r = subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "gates" / "coverage_gate.sh"),
            "--lang",
            "rust",
            "--floor",
            "0",
            "--external",
            probe,
            str(crate),
        ],
        cwd=crate,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = dict(line.split("=", 1) for line in seen.read_text().splitlines() if "=" in line)
    leaked = sorted(k for k in got if k.startswith(("CARGO_LLVM_COV", "__CARGO_LLVM_COV")))
    assert not leaked, f"the instrumented build's environment reached CMD: {leaked}"
    assert got.get("RUSTC_WRAPPER", "") == env.get("RUSTC_WRAPPER", ""), got.get("RUSTC_WRAPPER")
    assert got.get("CARGO_TARGET_DIR") == str(tmp_path / "callers-target")
    assert "CARGO_INCREMENTAL" not in got or "CARGO_INCREMENTAL" in env
    assert got.get("LLVM_PROFILE_FILE"), "CMD's instrumented runs must know where to profile"
    assert "%p" not in got["LLVM_PROFILE_FILE"], "one profile per process: thousands of files"


def test_a_scrubbed_test_environment_keeps_the_profile_destination(monkeypatch, tmp_path) -> None:
    """Under the external part, a test passing its own `env=` (a fake `curl` on PATH, a bare HOME)
    dropped LLVM_PROFILE_FILE: the instrumented binary's runs inside it were lost to coverage and
    left `default_*.profraw` in the test's cwd (14 in this checkout, 2026-10-06). conftest carries
    the destination into every explicit environment while the suite is measured."""
    monkeypatch.setenv("GOH_COVERAGE_BIN_DIR", str(tmp_path))
    monkeypatch.setenv("LLVM_PROFILE_FILE", str(tmp_path / "p-%8m.profraw"))
    r = subprocess.run(
        ["/usr/bin/env"], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True
    )
    assert f"LLVM_PROFILE_FILE={tmp_path / 'p-%8m.profraw'}" in r.stdout.splitlines(), r.stdout
    monkeypatch.delenv("GOH_COVERAGE_BIN_DIR")
    r = subprocess.run(
        ["/usr/bin/env"], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True
    )
    assert "LLVM_PROFILE_FILE" not in r.stdout, "outside a measured run an env is left as given"
