# GOH_LOAD_REAP_SCOPE
Area: python
Kind: setting
Order: 100
Default: unset

Under the goh suite only: the directory a load-reap SHAPE sweep is confined to (a command line must
name a path under it), set per test on the `load_orphans` worker (`tests/load_kit.py`). Unset under
the suite, a shape sweep takes nothing, so no test's sweep reaches another's children; outside the
suite it is not read.
