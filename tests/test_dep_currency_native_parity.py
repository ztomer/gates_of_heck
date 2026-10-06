"""`goh deps` reports what `check_dep_currency.py` reports (Phase N1).

Both arms on fixtures, crates.io answered through the shared cache seam: a pin below the graph, a
moved pin, an inherited requirement, a major behind (reported; fatal under --strict; fatal unless
the ratchet triaged it), drift, an empty tree, and --json. The semver table itself is pinned in the
crate (`crates/goh/src/deps/semver.rs`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

CHECK = REPO_ROOT / "checks" / "check_dep_currency.py"
PIN = (
    '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\nserde = "1.0.100"\n',
    'version = 3\n\n[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
    '[[package]]\nname = "ureq"\nversion = "3.4.2"\n\n[[package]]\nname = "serde"\nversion = "1.0.100"\n',
)
WORKSPACE = {
    "Cargo.toml": '[workspace]\nmembers = ["m"]\n\n[workspace.dependencies]\ntoml = "0.8"\n',
    "m/Cargo.toml": '[package]\nname = "m"\nversion = "0.1.0"\n\n[dependencies]\ntoml = { workspace = true }\n',
    "Cargo.lock": 'version = 3\n\n[[package]]\nname = "toml"\nversion = "0.8.23"\n',
}


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "r"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _env(tmp_path: Path) -> dict:
    cache = tmp_path / "crates-io"
    cache.mkdir(exist_ok=True)
    for name, version in {"ureq": "3.4.2", "serde": "1.0.228", "toml": "1.1.6"}.items():
        (cache / f"{name}.json").write_text(
            json.dumps({"fetched": time.time(), "version": version})
        )
    return dict(os.environ, GOH_CRATES_IO_CACHE=str(cache), GOH_LIVE="1")


def both(goh: Path, root: Path, env: dict, *args: str):
    run = lambda argv: subprocess.run(argv, cwd=root, capture_output=True, text=True, env=env)  # noqa: E731
    py = run([sys.executable, str(CHECK), *args])
    rs = run([str(goh), "deps", *args])
    return (py.returncode, py.stdout, py.stderr), (rs.returncode, rs.stdout, rs.stderr)


@pytest.mark.parametrize(
    "args", [[], ["--offline"], ["--strict"], ["--json"], ["--json", "--strict"]]
)
@pytest.mark.parametrize("shape", ["pin", "workspace", "empty"])
def test_every_shape_reports_the_same(goh: Path, tmp_path: Path, shape: str, args) -> None:
    files = {
        "pin": {"Cargo.toml": PIN[0], "Cargo.lock": PIN[1]},
        "workspace": WORKSPACE,
        "empty": {"README.md": "# none\n"},
    }[shape]
    root = _tree(tmp_path, files)
    py, rs = both(goh, root, _env(tmp_path), *args)
    assert rs == py


@pytest.mark.parametrize("triaged", ["", "ureq\n", "toml\n"])
def test_the_ratchet_reports_the_same(goh: Path, tmp_path: Path, triaged: str) -> None:
    root = _tree(tmp_path, WORKSPACE)
    ratchet = tmp_path / "ratchet.txt"
    ratchet.write_text(triaged)
    py, rs = both(goh, root, _env(tmp_path), "--ratchet", str(ratchet))
    assert rs == py
