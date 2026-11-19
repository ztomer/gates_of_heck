"""The external part's instrumented binary is the same bytes from any checkout (GOH.COVCARRY3).

A per-test profile is keyed on the instrumented binary's content hash (D-0011 option 4), and the
same inputs built 131f8444.. in one goh lane and 8218270f.. in another: each lane builds in its own
checkout (a push dir, a worktree) with its own build dir (keyed on the repo's path), and both paths
reached the binary -- the coverage map's compilation dir and every object's path in the debug map.
`_coverage_rust.sh` builds the external part with its workspace root remapped (cargo-llvm-cov's
`--remap-path-prefix`) and no debug info (coverage reads the coverage map, never DWARF), so two
checkouts of one tree build one binary; the report names each file under THIS checkout again, so
the external part still merges with the workspace's.

And REPRODUCIBLE (GOH.PROFKEYMISS): the same inputs build the same bytes. With cargo's default local
ThinLTO across codegen units, five builds of goh's tree gave four sha256s (GOH.PROFSTOREEMPTY), so
no land ever found the profiles the last one stored. A one-function crate has one codegen unit and
builds the same bytes either way, so the crate here is MODULES modules of FUNCTIONS branching
functions calling across modules: under the default its two builds differed in 12 of 12 pairs
(2026-11-19, 2-6 s a build), with `profile.dev.lto="off"` (the gate's) in none. The builds are the
gate's own, so a build flag added there that brings the drift back reds this test.
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

MODULES, FUNCTIONS = 64, 6


def _sources(src: Path) -> None:
    """MODULES modules of FUNCTIONS functions, each branching and calling into the next module:
    the shape whose default build was not reproducible. `go` takes every branch."""
    calls = " ^ ".join(f"m{i}::run(n)" for i in range(MODULES))
    main = "".join(f"mod m{i};\n" for i in range(MODULES))
    main += (
        "\nfn main() {\n"
        '    if std::env::args().nth(1).as_deref() == Some("go") {\n'
        "        for n in 0..2 {\n"
        f'            println!("{{}}", {calls});\n'
        "        }\n"
        "    }\n"
        "}\n"
    )
    (src / "main.rs").write_text(main)
    for i in range(MODULES):
        body = f"pub fn leaf(n: usize) -> u64 {{\n    (n as u64).wrapping_mul({i + 3})\n}}\n"
        for j in range(FUNCTIONS):
            body += (
                f"\nfn f{j}(n: usize) -> u64 {{\n"
                f"    let mut v: Vec<String> = (0..n + {j})\n"
                f'        .map(|k| format!("{i}-{{k}}"))\n'
                "        .collect();\n"
                "    v.sort();\n"
                f"    if v.len() > {j} {{\n"
                f"        crate::m{(i + j + 1) % MODULES}::leaf(v.len())\n"
                "    } else {\n"
                "        v.iter().map(|s| s.len() as u64).sum()\n"
                "    }\n"
                "}\n"
            )
        body += "\npub fn run(n: usize) -> u64 {\n    "
        body += " ^ ".join(f"f{j}(n)" for j in range(FUNCTIONS)) + "\n}\n"
        (src / f"m{i}.rs").write_text(body)


# Each `if`'s closing brace may carry a never-taken continuation region (a line is covered only by
# its own count, GOH.SPANFORGIVE): the floor is below what `go` covers, and the test is about the
# binary's bytes, not the number.
def _checkout(root: Path) -> Path:
    c = root / "binonly"
    (c / "src").mkdir(parents=True)
    (c / "Cargo.toml").write_text(
        '[package]\nname = "binonly"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
    )
    _sources(c / "src")
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
            "85",
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
        src = crate.resolve() / "src"
        want = {f"SF:{src / 'main.rs'}"} | {f"SF:{src / f'm{i}.rs'}" for i in range(MODULES)}
        assert set(parts[-1]) == want, sorted(set(parts[-1]) ^ want)[:4]
    assert shas[0] == shas[1], f"one tree, two binaries: {shas[0][:12]} != {shas[1][:12]}"
