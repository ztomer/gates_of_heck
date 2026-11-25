# GOH_PROFCARRY_TESTS
Area: local-ci
Kind: setting
Order: 220
Default: `on`

`off` is the kill switch of the binary-running tests' profile carry (`lib/prof_store.py`,
GOH.COVCARRY3): an instrumented run then neither stores each test's profile nor carries a
binary-running test by its stored one, so every such test runs under coverage. The `profcarry` audit
turning off stops the carry as well.
