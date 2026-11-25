# GOH_SUITE_CORES
Area: python
Kind: setting
Order: 50
Default: unset: the machine's core count (`sysctl -n hw.ncpu`, else `getconf _NPROCESSORS_ONLN`)

The core count `tools/pytest.sh` sizes its xdist workers by, capped at 12 (run 42950563400: `-n 12`
on a 3-core runner starved slow tests past their timeouts). A seam for its test; a value that is not
a positive integer is refused.
