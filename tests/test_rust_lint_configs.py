"""GOH_RUST_LINT_CONFIGS lints the configurations this host does not compile, through the cargo
each one needs (BACKLOG P1g).

Until now no test exercised GOH_RUST_LINT_CONFIGS at all. So, on a real crate:
* CALIBRATION: Linux-only dead code is invisible to the host clippy and red under a
  `--target x86_64-unknown-linux-musl` config -- the property the config exists for;
* GOH_RUST_LINT_CARGO runs the `--target` configs (only those) through the named cargo command --
  media_server's `cargo-zigbuild`, whose `zig cc` builds ring's C for musl;
* a named command that is not installed fails up front, before any clippy.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from test_rust_gate_scoped_cache import _crate, _git, _native, gates  # noqa: F401  # fixtures

TARGET = "x86_64-unknown-linux-musl"


def _installed() -> bool:
    if shutil.which("rustup") is None:
        return False
    out = subprocess.run(
        ["rustup", "target", "list", "--installed"], capture_output=True, text=True, check=False
    )
    return TARGET in out.stdout.split()


pytestmark = [
    pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed"),
    pytest.mark.skipif(platform.system() == "Linux", reason="the host IS the linux cfg here"),
    pytest.mark.skipif(not _installed(), reason=f"rustup target {TARGET} not installed"),
    pytest.mark.xdist_group("rust_scoped_cache"),
]

LINUX_ONLY = '#[cfg(target_os = "linux")]\nfn only_linux() {}\n\npub fn f() -> u64 {\n    1\n}\n'


def _repo(tmp_path: Path, lib: str, gatesrc: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _crate(repo, "a", lib=lib)
    (repo / ".gatesrc").write_text("GOH_DEPS_OFFLINE=1\nGOH_PROVEN=0\n" + gatesrc)
    (repo / ".gitignore").write_text("target/\n")
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"],
        cwd=repo / "crates" / "a",
        check=True,
        capture_output=True,
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    return repo


def _gate(repo: Path, path_prefix: Path | None = None) -> subprocess.CompletedProcess:
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "GIT_")) or k == "GOH_LIVE"
    }
    g = Path(os.environ["SCOPED_CACHE_GATES"])
    env.update(GOH_DIR=str(g), GOH_BIN=os.environ["SCOPED_CACHE_GOH"], GOH_RUST_GROUPS="crate")
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["bash", str(g / "gates" / "rust_gate.sh"), str(repo), str(repo / "crates" / "a")],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )


def test_calibration_the_linux_config_sees_what_the_host_cannot(tmp_path: Path) -> None:
    host = _gate(_repo(tmp_path / "h", LINUX_ONLY, ""))
    assert host.returncode == 0, (
        "the host clippy should not see linux-only code:\n" + host.stdout + host.stderr
    )
    cross = _gate(_repo(tmp_path / "x", LINUX_ONLY, f"GOH_RUST_LINT_CONFIGS='--target {TARGET}'\n"))
    out = cross.stdout + cross.stderr
    assert cross.returncode != 0, f"the linux config did not see linux-only dead code:\n{out}"
    assert f"clippy (--target {TARGET})" in out and "only_linux" in out, out


def _shim(tmp_path: Path, name: str = "xcargo") -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / f"{name}.argv"
    shim = bin_dir / name
    shim.write_text(f'#!/bin/sh\necho "$*" >> {log}\nexec cargo "$@"\n')
    shim.chmod(0o755)
    return bin_dir, log


def test_the_lint_cargo_runs_the_target_configs_and_only_those(tmp_path: Path) -> None:
    bin_dir, log = _shim(tmp_path)
    repo = _repo(
        tmp_path,
        "pub fn f() -> u64 {\n    1\n}\n",
        f"GOH_RUST_LINT_CONFIGS='--target {TARGET}:--no-default-features'\nGOH_RUST_LINT_CARGO=xcargo\n",
    )
    r = _gate(repo, bin_dir)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    calls = log.read_text().splitlines()
    assert calls == [f"clippy --locked --all-targets --target {TARGET} -- -D warnings"], calls
    assert f"clippy (--target {TARGET}) via xcargo" in out, out


def test_a_lint_cargo_that_is_not_installed_fails_up_front(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "pub fn f() -> u64 {\n    1\n}\n",
        f"GOH_RUST_LINT_CONFIGS='--target {TARGET}'\nGOH_RUST_LINT_CARGO=no-such-cargo-xyz\n",
    )
    r = _gate(repo)
    assert r.returncode != 0
    assert "GOH_RUST_LINT_CARGO='no-such-cargo-xyz' is not on PATH" in r.stderr, r.stderr
    assert f"clippy (--target {TARGET})" not in r.stdout
