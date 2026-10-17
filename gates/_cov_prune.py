"""_cov_prune.py -- delete the instrumented binaries the CURRENT build did not produce.

cargo-llvm-cov exports from every executable in the build dir whose name is a workspace target's,
whatever its metadata hash. The build dir outlives a build (BACKLOG 5.1), so a member whose hash
changed (a version bump, an edition, a dependency) leaves its old binaries beside the new ones, and
they carry the old source: removed code read uncovered, at lines the current file may not have
(sys_updater, 2026-10-17: 96.44% read 92.32%). Cargo's own JSON messages name every artifact of the
build just made -- fresh units included -- so anything else of a member's name is a previous
build's. A current bin's deps copy is kept by identity with its uplifted file (a hardlink, or a
byte-identical copy), so cargo never relinks it.

usage: _cov_prune.py <cargo-json>     (run in the project: it asks cargo for the names)

The directories judged are the ones the REPORT reads -- `$CARGO_LLVM_COV_TARGET_DIR/debug` and
`$CARGO_LLVM_COV_BUILD_DIR/debug`, as `cargo llvm-cov show-env` sets them -- never a path the caller
computes: cargo-llvm-cov nests its build under `llvm-cov-target/`, and a computed path that missed
it pruned nothing and passed. Every executable the build names must lie under them, or the build
is not what the report reads and this refuses.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]


import filecmp
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP = (".d", ".rlib", ".rmeta", ".o")


def main(argv: list[str]) -> int:
    json_path = Path(argv[1])
    # The names cargo-llvm-cov matches: every workspace package and every target of one.
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout)  # fmt: skip
    targets = {p["name"] for p in meta["packages"]} | {
        t["name"] for p in meta["packages"] for t in p["targets"]
    }
    names = "|".join(
        sorted(re.escape(n).replace(r"\-", "-").replace("-", "(-|_)") for n in targets)
    )
    member = re.compile(rf"^(lib)?({names})(-[0-9a-f]+)?$")
    keep: set[Path] = set()
    artifacts = 0
    for line in json_path.read_text().splitlines():
        if not line.startswith("{"):
            continue
        msg = json.loads(line)
        if msg.get("reason") != "compiler-artifact":
            continue
        artifacts += 1
        for f in [*msg.get("filenames", []), msg.get("executable") or ""]:
            if f:
                keep.add(Path(f))
    if artifacts == 0:
        print(f"_cov_prune: {json_path} names no artifact -- refusing to judge the build dir")
        return 1
    roots = [
        Path(os.environ[v]) / "debug"
        for v in ("CARGO_LLVM_COV_TARGET_DIR", "CARGO_LLVM_COV_BUILD_DIR")
        if os.environ.get(v)
    ]
    exes = [k for k in keep if os.access(k, os.X_OK) and k.suffix not in SKIP and k.is_file()]
    if not roots or not exes:
        print(f"_cov_prune: no show-env directories ({roots}) or no executable in {json_path}")
        return 1
    stray = [str(k) for k in exes if not any(k.is_relative_to(r) for r in roots)]
    if stray:
        print(f"_cov_prune: the build is not where the report reads ({roots}): {stray[:3]}")
        return 1
    removed = 0
    for f in (f for r in roots if r.is_dir() for f in _walk(r)):
        if f.suffix in SKIP or not f.is_file() or not os.access(f, os.X_OK):
            continue
        if not member.match(f.name.split(".")[0]) or f in keep:
            continue
        if any(k.exists() and (os.path.samefile(k, f) or _same_bytes(k, f)) for k in keep):
            continue
        f.unlink()
        removed += 1
    if removed:
        print(f"_cov_prune: removed {removed} binaries of a previous build under {roots}")
    return 0


def _walk(root: Path):
    """Every file the report's walk can reach (cargo-llvm-cov skips these directories too)."""
    for d, subdirs, files in os.walk(root):
        subdirs[:] = [s for s in subdirs if s not in ("incremental", ".fingerprint", "fingerprint")]
        for name in files:
            yield Path(d) / name


def _same_bytes(a: Path, b: Path) -> bool:
    return a.stat().st_size == b.stat().st_size and filecmp.cmp(a, b, shallow=False)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
