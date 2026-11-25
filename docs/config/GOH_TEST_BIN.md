# GOH_TEST_BIN
Area: coverage
Kind: setting
Default: unset (the suite builds one)

A `goh` binary goh's own suite uses as it is in place of building one (`tests/conftest.py`): the
coverage gate's instrumented build, by this repo's `GOH_COV_RUST_EXTERNAL`. Not executable is an
error.
