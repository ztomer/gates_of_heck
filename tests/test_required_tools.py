"""A gate whose tool is missing FAILS; it never warns and passes.

antiknob/divoom, 2026-10-05: `rust_gate.sh` printed "cargo-machete not installed -- unused
dependencies were NOT checked" and `swift_gate.sh` "swiftlint not installed -- lint gate skipped",
and both exited 0. A step that did not run printed a warning nobody reads above a green result:
the same shape as a gate that reports compliance having inspected nothing. Each test removes
exactly ONE tool from PATH, so the failure is that tool's and nothing else's.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT


def path_without(tool: str, into: Path) -> str:
    """A PATH holding every executable the real PATH does, except `tool`. Symlinks, so rustup's
    proxies still dispatch on their own names."""
    into.mkdir()
    for d in os.environ["PATH"].split(os.pathsep):
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            src = os.path.join(d, name)
            if name == tool or (into / name).exists() or not os.access(src, os.X_OK):
                continue
            if os.path.isfile(src):
                (into / name).symlink_to(src)
    assert shutil.which(tool, path=str(into)) is None
    return str(into)


def run(script: str, cwd: Path, path: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_")}
    env.update(PATH=path, GOH_DIR=str(REPO_ROOT))
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / script), str(cwd)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_rust_gate_fails_without_cargo_machete(tmp_path):
    crate = tmp_path / "c"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (crate / "src" / "lib.rs").write_text("")
    got = run("rust_gate.sh", crate, path_without("cargo-machete", tmp_path / "bin"))
    assert got.returncode != 0, got.stdout + got.stderr
    assert "cargo-machete is not installed" in got.stdout + got.stderr, got.stdout + got.stderr


def test_swift_gate_fails_without_swiftlint(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "Package.swift").write_text(
        '// swift-tools-version:5.9\nimport PackageDescription\nlet package = Package(name: "x", targets: [])\n'
    )
    got = run("swift_gate.sh", proj, path_without("swiftlint", tmp_path / "bin"))
    assert got.returncode != 0, got.stdout + got.stderr
    assert "swiftlint is not installed" in got.stdout + got.stderr, got.stdout + got.stderr


def test_shell_lint_fails_without_shellcheck(tmp_path):
    """It degraded to `bash -n` and printed OK: syntax-only under a green "shell lint" line."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.sh").write_text("#!/bin/bash\necho ok\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_")}
    env["PATH"] = path_without("shellcheck", tmp_path / "bin")
    got = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "checks" / "check_shell_lint.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode != 0, got.stdout + got.stderr
    assert "shellcheck is not installed" in got.stdout + got.stderr, got.stdout + got.stderr
