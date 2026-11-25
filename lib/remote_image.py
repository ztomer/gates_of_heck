"""The remote test worker's image and its pins (GOH.REMOTEWORKERS; split out of
tools/remote_shard.py for its line cap, GOH.REMOTEWIRE): the build args read from goh's ONE
version manifest (gates/required_tools.tsv), the image tag that changes with them, and the parity
check of what a running worker says it carries against the manifest and the Mac's Python.

The remote test worker's image and pins (GOH.REMOTEWORKERS, split from `tools/remote_shard.py` for
its cap): the build args read from `gates/required_tools.tsv`, the image tag that changes with
them, and the parity check of a running worker's recorded versions against the manifest and the
Mac's Python. Pinned by `test_remote_shard.py`, `test_remote_worker_image.py`.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def manifest_pins(tsv: Path) -> dict[str, str]:
    """The suite's pip modules and their exact pins (`=X.Y.Z` rows of the goh-suite layer)."""
    pins: dict[str, str] = {}
    for line in tsv.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[0] == "goh-suite" and f[2] == "pip" and f[5].startswith("="):
            pins[f[1].lower()] = f[5][1:]
    return pins


def version_drift(text: str, pins: dict[str, str]) -> list[str]:
    """Each way the worker's recorded versions differ from the manifest and the Mac's Python."""
    have: dict[str, str] = {}
    python = ""
    for line in text.splitlines():
        if line.startswith("Python "):
            python = line.split()[1]
        elif "==" in line:
            name, ver = line.split("==", 1)
            have[name.strip().lower().replace("_", "-")] = ver.strip()
    mac = ".".join(map(str, sys.version_info[:3]))
    out = [] if python == mac else [f"python {python or 'absent'} (Mac {mac})"]
    out += [
        f"{m} {have.get(m, 'absent')} (manifest {v})" for m, v in pins.items() if have.get(m) != v
    ]
    return out


def build_args(tsv: Path, tree: Path) -> list[str]:
    """`KEY=VALUE` for every Dockerfile arg goh's manifest owns: each pinned pip module, shellcheck
    (with its linux.x86_64 checksum from the goh-floors table), cargo-llvm-cov and swiftlint."""
    floors: dict[str, str] = {}
    for line in tsv.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[5] != "-":
            floors.setdefault(f[1].lower(), f[5].lstrip("="))
    args = {m.upper().replace("-", "_") + "_VERSION": v for m, v in manifest_pins(tsv).items()}
    for tool in ("shellcheck", "cargo-llvm-cov", "swiftlint"):
        args[tool.upper().replace("-", "_") + "_VERSION"] = floors[tool]
    table = tree / ".github" / "actions" / "goh-floors" / "shellcheck.sha256"
    for line in table.read_text().splitlines() if table.exists() else []:
        f = line.split("\t")
        if len(f) == 4 and f[1] == floors["shellcheck"] and f[2] == "linux.x86_64":
            args["SHELLCHECK_SHA256"] = f[3]
    return [f"{k}={v}" for k, v in sorted(args.items())]


def image_tag(tree: Path, args: list[str]) -> tuple[str, Path]:
    """The image's tag: its Dockerfile's content AND the build args, so a raised pin is new."""
    dockerfile = tree / "tools" / "remote_worker" / "Dockerfile"
    digest = hashlib.sha256(dockerfile.read_bytes() + "\n".join(args).encode()).hexdigest()
    return f"goh-remote-worker:{digest[:12]}", dockerfile
