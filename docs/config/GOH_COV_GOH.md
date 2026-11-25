# GOH_COV_GOH
Area: coverage
Kind: setting
Order: 50
Default: unset

The coverage run's instrumented `goh` (this repo's `GOH_COV_RUST_EXTERNAL` names it beside
`GOH_TEST_BIN`). `gates/_goh_bin.sh` answers with it when its `goh source-tree` stamp is the one the
resolution wants -- never a build of other source or `dirty` -- so a test that drives goh through a
gate script leaves a profile too (GOH.COVGOHBIN). A hermetic test child keeps it.
