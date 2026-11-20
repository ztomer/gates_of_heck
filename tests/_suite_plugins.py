"""The suite's own plugins, imported by conftest's pytest_configure through one name.

Each is a module of tests/ with hooks of its own; pytest imports every name in `pytest_plugins` of
a module it registers. tests/conftest.py sits at the file-length cap, so a new plugin is a line
here, not there.

* `_share_tokens`: the live worker share (GOH.SHAREGROW).
* `_fs_churn`: the run's file events, counted and held under a ceiling (GOH.FSCHURN2).
"""

pytest_plugins = ["_share_tokens", "_fs_churn"]
