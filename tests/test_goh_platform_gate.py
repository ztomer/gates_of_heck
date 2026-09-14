"""The goh-build platform gate: 64-bit only, macOS Apple silicon only.

gates_of_heck MUST remain Linux compatible — Linux x86_64/aarch64 pass.
Teeth: delete any `exit 1` in scripts/build-goh.sh and the matching test goes red.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-goh.sh"


def _run(os_name: str, arch: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "GOH_BUILD_OS": os_name,
            "GOH_BUILD_ARCH": arch,
            "GOH_BUILD_GATE_ONLY": "1",
        },
    )


@pytest.mark.parametrize("arch", ["x86_64", "amd64"])
def test_macos_intel_is_rejected(arch: str) -> None:
    r = _run("Darwin", arch)
    assert r.returncode == 1, f"macOS/{arch} should be rejected, got {r.returncode}"
    assert "Intel" in r.stderr
    assert "Linux x86_64 remains supported" in r.stderr


@pytest.mark.parametrize("arch", ["i686", "i386", "armv7l", "armhf"])
def test_32bit_is_rejected_on_every_os(arch: str) -> None:
    for os_name in ("Linux", "Darwin"):
        r = _run(os_name, arch)
        assert r.returncode == 1, f"{os_name}/{arch} should be rejected"
        assert "64-bit only" in r.stderr or "Intel" in r.stderr


@pytest.mark.parametrize(
    "os_name,arch",
    [
        ("Darwin", "arm64"),
        ("Linux", "x86_64"),
        ("Linux", "amd64"),
        ("Linux", "aarch64"),
    ],
)
def test_supported_platforms_pass_the_gate(os_name: str, arch: str) -> None:
    r = _run(os_name, arch)
    assert r.returncode == 0, f"{os_name}/{arch} should pass, stderr={r.stderr}"
    assert "gate ok" in r.stdout


@pytest.mark.parametrize("os_name", ["FreeBSD", "OpenBSD", "SunOS", "Windows_NT"])
def test_unsupported_os_is_rejected(os_name: str) -> None:
    r = _run(os_name, "x86_64")
    assert r.returncode == 1, f"{os_name} should be rejected, got {r.returncode}"
    assert "Unsupported OS" in r.stderr
