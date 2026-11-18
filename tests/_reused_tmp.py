"""`reused_mkdtemp(prefix, dir)`: a per-process temp dir at a name the next run's worker reuses.

GOH.FSCHURN (2026-11-18): fseventsd sat at one core and 11.7 GB, and goh was 78-99% of the file
events on /. Unique directory names grew it (278 MB in 97 s of churn) where the same operations
under one reused name stayed in the ambient band. `tempfile.mkdtemp` hands every process a name
never seen before, and each test helper's per-process dir (a git seed, a template home, a cargo
shim) took one in every run. This one is `<prefix><worker>.<n>`: the xdist worker
(PYTEST_XDIST_WORKER, `main` outside xdist) and the n-th such dir this process asked for under
that parent. It is made with mkdir, so a name already taken -- a concurrent process of the same
worker id, a killed run's leftover -- is never shared: that call gets a fresh mkdtemp name, as
before. The run's own root is a slot (gates/_pytest_root.sh, tests/suite_home.py), so the whole
path repeats. Pinned by tests/test_reused_temp_names.py.
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path

_ASKED: Counter[tuple[str, str]] = Counter()


def reused_mkdtemp(prefix: str, dir: str | Path | None = None) -> str:
    parent = str(dir) if dir is not None else tempfile.gettempdir()
    n = _ASKED[(prefix, parent)]
    _ASKED[(prefix, parent)] += 1
    path = os.path.join(parent, f"{prefix}{os.environ.get('PYTEST_XDIST_WORKER') or 'main'}.{n}")
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        return tempfile.mkdtemp(prefix=prefix, dir=parent)
    return path
