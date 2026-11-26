"""The suite's own plugins, imported by conftest's pytest_configure through one name.

Each is a module of tests/ with hooks of its own; pytest imports every name in `pytest_plugins` of
a module it registers. tests/conftest.py sits at the file-length cap, so a new plugin is a line
here, not there.

* `_share_tokens`: the live worker share (GOH.SHAREGROW).
* `_fs_churn`: the run's file events, counted and held under a ceiling (GOH.FSCHURN2).
* `_land_remote`: the land gate's remote share (GOH.REMOTEGATE, lib/remote_gate.py).
* `_worker_feed`: the pre-step's run hands the Linux worker units of its queue (GOH.REMOTECARRY).
* `_xdist_usage`: a worker's collection error is the run's (GOH.REMOTEREDSILENT).
* `_kept_store`: the kept verdict rows, seeded from and merged into the real goh-cache.db once by
  the controller (GOH.VERDICTDB).
* `_db_on_disk`: a test's goh.db journals in memory (GOH.FSEVQUIET).
* `_quiet_tmp`: no `current` link a test, and a module's clones are pooled (GOH.FSEVQUIET).
* here: a `process_template` is the RUN's inside a session (tests/_tree_clone.py, GOH.FSCHURN3),
  and then a pytest a test starts takes a basetemp root of its own (tests/suite_tmp.py `own_root`,
  GOH.NESTEDBASETEMP): no conftest hook or fixture asks for the basetemp before this one runs.
* here, first: pytest's rewritten test files are kept between runs (tests/_rewrite_store.py,
  GOH.PYHARNESS), the plugins below and every test file among them.
* here, first: INT and QUIT, inherited ignored (the rust gate's `( suite ) &`), are put back before
  a test starts a child (tests/_inherited_signals.py, GOH.RUSTOVERLAP).
* `_test_work`: each test's CPU, processes, git repos, cargo builds and tree walks, recorded and
  held under a ceiling (GOH.TESTWORK, lib/test_work.py).
* `_land_waive`: a land's reds that are main's or a newer rule's, waived by a reading on main or
  warned (GOH.MAINREDWAIVE, GOH.RULEINFLIGHT, lib/land_waive.py).
"""

import os
from pathlib import Path

import _closure_clones
import _fixture_copies
import _inherited_signals
import _quiet_git
import _quiet_tmp
import _rewrite_store
from _clone_copies import install
from _tree_clone import run_wide
from suite_tmp import own_root

_inherited_signals.restore()
_rewrite_store.install()

pytest_plugins = [
    "_share_tokens",
    "_fs_churn",
    "_land_remote",
    "_worker_feed",
    "_xdist_usage",
    "_kept_store",
    "_land_waive",
    "_db_on_disk",
    "_quiet_tmp",
    "_test_work",
]


def pytest_configure(config) -> None:
    install()  # shutil's copies are clones (tests/_clone_copies.py, GOH.FSEVQUIET)
    _closure_clones.install()
    _fixture_copies.install()
    _quiet_tmp.install()
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):  # the run's root (tests/suite_tmp.py)
        run_wide(Path(os.environ["PYTEST_DEBUG_TEMPROOT"]))
        _quiet_git.install(os.environ["PYTEST_DEBUG_TEMPROOT"])  # GOH.FSEVQUIET
    own_root(config)  # after run_wide: the run's templates stay the worker's, shared
