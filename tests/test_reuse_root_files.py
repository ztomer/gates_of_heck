"""What stays a file under the reuse root after GOH.VERDICTDB moved its records into the databases,
each decided and registered with its reason (lib/state_bounds.py REGISTRY), never left to drift.

The verdict records moved to goh-cache.db (lib/reuse_store.py) and the audit to goh.db
(lib/reuse_audit_store.py). Three directories stay files, by decision (GOH.VERDICTDB V1 step 2):
`profstore` (llvm-cov reads the .profdata files from disk: handover plan (e)), `profreach` (an
index of profstore, one file per key, its validity and its prune both read from profstore's
directory) and `binreach` (a 200-row ledger, one writer). A new directory a module keeps under the
root must be registered the same way, or moved, so nothing grows there unbounded and unseen.
"""

from __future__ import annotations

import re
import sys

from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import state_bounds  # lib/, after its path

DIRS = ("lib", "scripts", "tools", "gates", "hooks", "checks")
NAMED = re.compile(r'\b(?:reuse|reuse_store)\.root\(\) / "([A-Za-z0-9_.-]+)"')


def kept_dirs() -> dict[str, set[str]]:
    """{directory under the reuse root: the files that name it}."""
    out: dict[str, set[str]] = {}
    for top in DIRS:
        for f in sorted((REPO_ROOT / top).rglob("*.py")):
            for seg in NAMED.findall(f.read_text(encoding="utf-8")):
                out.setdefault(seg, set()).add(f.relative_to(REPO_ROOT).as_posix())
    return out


def test_every_directory_kept_under_the_reuse_root_is_registered() -> None:
    found = kept_dirs()
    assert {"profstore", "profreach", "binreach", "audit"} <= set(found), found  # the scan sees
    kept = {k for k, v in state_bounds.REGISTRY.items() if isinstance(v, state_bounds.Kept)}
    bad = {seg: sorted(files) for seg, files in found.items() if f"reuse/{seg}" not in kept}
    assert bad == {}, f"unregistered under the reuse root (lib/state_bounds.py REGISTRY): {bad}"


def test_the_decided_files_say_why() -> None:
    for seg in ("profreach", "binreach"):
        why = state_bounds.REGISTRY[f"reuse/{seg}"].why
        assert "GOH.VERDICTDB" in why, (seg, why)
