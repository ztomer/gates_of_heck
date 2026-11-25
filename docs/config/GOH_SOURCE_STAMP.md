# GOH_SOURCE_STAMP
Area: runtime
Kind: setting
Order: 530
Default: the committed tree at HEAD

The source the binary was built from, baked in at build time (`crates/goh/build.rs`;
`scripts/build-goh.sh` passes the export's stamp) and printed by `goh source-tree`. Not user config.
