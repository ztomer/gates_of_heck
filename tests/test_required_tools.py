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

from conftest import REPO_ROOT, hermetic_env


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
    env = hermetic_env()
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


def test_shell_lint_fails_without_shellcheck(tmp_path, goh):
    """It degraded to `bash -n` and printed OK: syntax-only under a green "shell lint" line."""
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.sh").write_text("#!/bin/bash\necho ok\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    env = hermetic_env()
    env["PATH"] = path_without("shellcheck", tmp_path / "bin")
    got = subprocess.run(
        [str(goh), "shell-lint"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert got.returncode != 0, got.stdout + got.stderr
    assert "shellcheck is not installed" in got.stdout + got.stderr, got.stdout + got.stderr


def _runner_without(module: str, into: Path) -> str:
    """A GOH_PY_RUNNER that is `python3 -m` for every module but `module`, which it cannot import:
    an interpreter upgrade leaves exactly this (2026-10-10: Homebrew's 3.15 had pytest, not ruff)."""
    into.mkdir()
    runner = into / "runner"
    runner.write_text(
        f'#!/bin/sh\n[ "$1" = {module} ] && {{ echo "No module named {module}" >&2; exit 1; }}\n'
        '[ "$2" = --version ] && { echo "$1 0.0"; exit 0; }\n'  # every other tool is present
        'exec python3 -m "$@"\n'
    )
    runner.chmod(0o755)
    return str(runner)


@pytest.mark.parametrize("module", ["ruff", "pytest"])
def test_py_gate_refuses_a_runner_that_cannot_import_its_tools(tmp_path, module):
    """It ran the step and failed it as `ruff check` with "No module named ruff" -- a finding
    about the code, read off a missing tool, with no install line anywhere."""
    pkg = tmp_path / "p"
    pkg.mkdir()
    (pkg / "m.py").write_text("X = 1\n")
    env = hermetic_env(
        GOH_DIR=str(REPO_ROOT), GOH_PY_RUNNER=_runner_without(module, tmp_path / "b")
    )
    got = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_gate.sh"), str(pkg)],
        cwd=pkg, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    out = got.stdout + got.stderr
    assert got.returncode != 0, out
    assert f"{module} is not available through" in out, out
    assert f"python3 -m pip install {module}" in out, out


def test_py_staged_refuses_a_runner_that_cannot_import_ruff(tmp_path, repo):
    (repo / "m.py").write_text("X = 1\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "m.py"], check=True, env=hermetic_env(drop_git=True)
    )
    env = hermetic_env(
        GOH_DIR=str(REPO_ROOT), GOH_PY_RUNNER=_runner_without("ruff", tmp_path / "b")
    )
    got = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "py_staged.sh"), str(repo)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    out = got.stdout + got.stderr
    assert got.returncode != 0, out
    assert "ruff is not available through" in out and "python3 -m pip install ruff" in out, out
