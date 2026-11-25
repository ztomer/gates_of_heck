"""A wrapper first on PATH that runs another binary is keyed as THAT one (GOH.VERDICTCARRY3).

tests/_drift_guard.py puts a per-run cargo shim (goh-drift-cargo.XXXX) first on every suite run's
PATH. lib/gate_identity.py `tools()` fingerprinted the shim, so each suite run had its own tools
key -- tree cad06549 held 6 suite records under 6 keys -- and no lookup outside the suite
(`reuse.py full-suite`, the release kit's check) matched a record the suite wrote. A shim says
which binary it runs on one of its first lines, `SHIM_MARK` and an absolute path, and the identity
follows it; a nested suite's shim runs the outer suite's, so the chain is followed (a bounded
number of hops). Read from the script's first bytes only: no spawn. Pinned by
tests/test_verdict_carry_hermetic.py.

GOH.VERDICTCARRY3: a tool on PATH as a verdict identity keys it -- `fingerprint` (where it
resolves, size, mtime: no spawn) and `past_shims`, which follows a wrapper naming the binary it
runs (`# goh-shim-execs: PATH`, a nested suite's chain included) to that binary: the suite's
per-run cargo drift shim (`tests/_drift_guard.py`) gave every suite run its own tools key, so no
full-suite record was ever found. Read by `gate_identity.py tools()`.
"""

from __future__ import annotations

import os

SHIM_MARK = "# goh-shim-execs: "
HOPS = 8


def fingerprint(path: str) -> str:
    """Where `path` resolves and which bytes that is (size and mtime: no spawn)."""
    real = os.path.realpath(path)
    try:
        st = os.stat(real)
    except OSError:
        return f"{path} -> {real} unreadable"
    return f"{path} -> {real} {st.st_size} {st.st_mtime_ns}"


def past_shims(hit: str | None) -> str | None:
    """`hit`, or the binary its shim chain names (None stays None: the tool is absent)."""
    for _ in range(HOPS if hit else 0):
        try:
            with open(hit, "rb") as f:
                lines = f.read(512).decode("utf-8", "replace").splitlines()[:4]
        except OSError:
            return hit
        named = [ln[len(SHIM_MARK) :].strip() for ln in lines[1:] if ln.startswith(SHIM_MARK)]
        if not (lines and lines[0].startswith("#!") and named and os.path.isabs(named[0])):
            return hit
        hit = named[0]
    return hit
