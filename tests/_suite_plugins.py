"""The suite's own plugins, imported by conftest's pytest_configure through one name.

Each is a module of tests/ with hooks of its own; pytest imports every name in `pytest_plugins` of
a module it registers. tests/conftest.py sits at the file-length cap, so a new plugin is a line
here, not there.

* `_share_tokens`: the live worker share (GOH.SHAREGROW).
* `_fs_churn`: the run's file events, counted and held under a ceiling (GOH.FSCHURN2).
* `_land_remote`: the land gate's remote share (GOH.REMOTEGATE, lib/remote_gate.py).
* `_xdist_usage`: a worker's collection error is the run's (GOH.REMOTEREDSILENT).
* `_kept_store`: the kept verdict rows, seeded from and merged into the real goh-cache.db once by
  the controller (GOH.VERDICTDB).
* here: a `process_template` is the RUN's inside a session (tests/_tree_clone.py, GOH.FSCHURN3),
  and then a pytest a test starts takes a basetemp root of its own (tests/suite_tmp.py `own_root`,
  GOH.NESTEDBASETEMP): no conftest hook or fixture asks for the basetemp before this one runs.
* `_land_waive`: a land's reds that are main's or a newer rule's, waived by a reading on main or
  warned (GOH.MAINREDWAIVE, GOH.RULEINFLIGHT, lib/land_waive.py).
"""

import os
from pathlib import Path

from _tree_clone import run_wide
from suite_tmp import own_root

pytest_plugins = [
    "_share_tokens",
    "_fs_churn",
    "_land_remote",
    "_xdist_usage",
    "_kept_store",
    "_land_waive",
]


def pytest_configure(config) -> None:
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):  # the run's root (tests/suite_tmp.py)
        run_wide(Path(os.environ["PYTEST_DEBUG_TEMPROOT"]))
    own_root(config)  # after run_wide: the run's templates stay the worker's, shared
