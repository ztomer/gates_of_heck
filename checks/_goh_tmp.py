"""`goh_tmp()` for checks/: lib/tmp_root.py's ONE parent of every temp root goh makes.

A check's scratch (a probe's fixture repos, a sweep's skeleton) went straight into the bare
`$TMPDIR` -- under the suite its run root, but from a real gate the user's own temp directory,
outside any root a run owns, removes or counts (GOH.FSCHURN3: `goh-empty-scope.*`). Every
tempfile maker in checks/ names `dir=goh_tmp()`, which tests/tmp_root_lint.py holds. Loaded from
lib/ by path, so lib/ is never put on a check's import path (its module names would shadow the
check's own).

Lib: `goh_tmp()` for checks/, lib/tmp_root.py loaded by path (GOH.FSCHURN3): every tempfile maker
in checks/ names `dir=goh_tmp()`, so a probe's or sweep's scratch is never made in the bare
`$TMPDIR` outside a run's roots. Pinned by `test_tmp_noindex.py`.
"""

from __future__ import annotations

import importlib.util
import os

# Beside this checks/ (goh's own tree), else GOH_DIR's: a test that copies a few checks into a
# fixture of its own copies no lib/.
_HOMES = (
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.environ.get("GOH_DIR", ""),
)
_FOUND = [
    p for h in _HOMES if h for p in [os.path.join(h, "lib", "tmp_root.py")] if os.path.isfile(p)
]
if not _FOUND:
    raise ImportError(f"lib/tmp_root.py is in neither of {[h for h in _HOMES if h]}")
_PATH = _FOUND[0]
_SPEC = importlib.util.spec_from_file_location("_goh_lib_tmp_root", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
goh_tmp = _MOD.goh_tmp
