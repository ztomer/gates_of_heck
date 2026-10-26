"""A RECORDED fake cargo for tests of a gate's scoping and reporting logic (G2e, seam-and-cover).

What rust_gate.sh's proven cache needs from cargo is its METADATA (`locate-project`, `metadata`,
`-V`, `generate-lockfile`), which the real cargo answers in milliseconds without compiling. What
costs is compiling -- clippy, check, build, test, and the fmt/machete/audit scans -- and no
scoping verdict reads their output: the cache asks whether the group RAN and what its inputs
were. So those subcommands are recorded, one line each, and answer green; everything else is the
real cargo. Measured 2026-10-25: tests/test_rust_gate_scoped_cache.py spent 179 s of the suite's
2075 test-seconds in real compiles of three one-line crates.

What the fake cannot see is what a real compiler READ (dep-info, `*.d`), so the tests of THAT --
a build reading outside its scope, a path dependency's own tests, a keyed gates root -- stay on
real cargo: they are the end-to-end proof that the cache trusts the compiler's own record.
"""

from __future__ import annotations

import shutil
from pathlib import Path

REAL_CARGO = shutil.which("cargo") or "cargo"

# The subcommands that compile or scan, and so cost; each is recorded and answers green.
RECORDED = ("clippy", "check", "build", "test", "fmt", "machete", "audit")


def install(where: Path) -> tuple[Path, Path]:
    """Write the fake into `where/bin/cargo`; return (its bin dir, its call log)."""
    bin_dir = where / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log = where / "cargo.calls"
    fake = bin_dir / "cargo"
    fake.write_text(
        "#!/bin/bash\n"
        f'case "$1" in {"|".join(RECORDED)})\n'
        f'    printf \'%s\\t%s\\n\' "$PWD" "$*" >> "{log}"\n'
        "    exit 0 ;;\n"
        "esac\n"
        f'exec "{REAL_CARGO}" "$@"\n'
    )
    fake.chmod(0o755)
    return bin_dir, log


def calls(log: Path, sub: str) -> list[str]:
    """The directories `cargo <sub>` was recorded in, in order."""
    if not log.exists():
        return []
    rows = [line.split("\t", 1) for line in log.read_text().splitlines()]
    return [cwd for cwd, argv in rows if argv.split(" ", 1)[0] == sub]
