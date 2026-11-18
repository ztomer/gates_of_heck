"""The external part's instrumented binary is the same bytes from any checkout (GOH.COVCARRY3).

A per-test profile is keyed on the instrumented binary's content hash (D-0011 option 4), and the
same inputs built 131f8444.. in one goh lane and 8218270f.. in another: each lane builds in its own
checkout (a push dir, a worktree) with its own build dir (keyed on the repo's path), and both paths
reached the binary -- the coverage map's compilation dir and every object's path in the debug map.
`_coverage_rust.sh` builds the external part with its workspace root remapped (cargo-llvm-cov's
`--remap-path-prefix`) and no debug info (coverage reads the coverage map, never DWARF), so two
checkouts of one tree build one binary; the report names each file under THIS checkout again, so
the external part still merges with the workspace's.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None
    or subprocess.run(
        ["cargo", "llvm-cov", "--version"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    ).returncode,
    reason="cargo llvm-cov not available",
)

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


def _checkout(root: Path) -> Path:
    c = root / "binonly"
    (c / "src").mkdir(parents=True)
    (c / "Cargo.toml").write_text(
        '[package]\nname = "binonly"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    (c / "src" / "main.rs").write_text(MAIN)
    subprocess.run(
        ["cargo", "generate-lockfile", "-q", "--offline"],
        cwd=c,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    return c


def _gate(crate: Path, out: Path) -> subprocess.CompletedProcess:
    bin_ = '"$GOH_COVERAGE_BIN_DIR/binonly"'
    ext = f'shasum -a 256 {bin_} >"{out}" && {bin_} go'
    return subprocess.run(
        [
            "/bin/bash",
            str(REPO_ROOT / "gates" / "coverage_gate.sh"),
            "--lang",
            "rust",
            "--floor",
            "90",
            "--external",
            ext,
            str(crate),
        ],
        cwd=crate,
        capture_output=True,
        text=True,
        env=hermetic_env(),
        timeout=patience(600),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_two_checkouts_of_one_tree_build_one_instrumented_binary(tmp_path: Path) -> None:
    shas, parts = [], []
    for name in ("lane-one", "another-lane"):
        crate = _checkout(tmp_path / name)
        out = tmp_path / f"{name}.sha"
        r = _gate(crate, out)
        # Green on a floor of 90: the binary's only coverage is its external run, so the report
        # counted it under this checkout's own file names.
        assert r.returncode == 0, r.stdout + r.stderr
        shas.append(out.read_text().split()[0])
        part = crate / "target" / "llvm-cov" / "lcov-parts" / "part-external.info"
        parts.append([ln for ln in part.read_text().splitlines() if ln.startswith("SF:")])
        assert parts[-1] == [f"SF:{crate.resolve() / 'src' / 'main.rs'}"], parts[-1]
    assert shas[0] == shas[1], f"one tree, two binaries: {shas[0][:12]} != {shas[1][:12]}"
