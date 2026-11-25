# GOH_COV_FLOORS_JSON
Area: coverage
Kind: setting
Order: 30
Default: unset

JSON file with per-target + per-file floors (`--floors-json`); a relative path is read from where it
was given (made absolute before the mode `cd`s into the project). An `exempt` key names a file as
lcov does: relative to the PROJECT directory (`src/x.rs` for project `rust/`), or absolute. Rust
mode also reads two maps that lower ONE crate or file while the rest stay at the uniform floor
(GOH.CRATEFLOOR): `"crate_floors": {"<crate>": {"floor": 73, "reason": "..."}}` (a crate is a
directory or package name of a Cargo.toml; the crates it does not name stay at `GOH_COV_FLOOR_RUST`
/ `--floor`, judged together; `GOH_COV_FLOOR_RUST` and this file may be set together) and
`"file_floors": {"crates/x/src/y.rs": {"floor": 62.3, "reason": "..."}}` (lowers `file_floor` for
that file; a key is relative to the project or absolute). Each entry needs a numeric `floor` and a
non-empty `reason` (refused, exit 2); it may record its runs of one tree, `"measured": [89.74,
89.67, 89.82]` (two or more percents, GOH.COVNONDET), and a floor above the lowest of them is
refused too (exit 2: a run at that low end reds an unchanged tree). An entry must be BELOW the
uniform floor it lowers, and must name a crate of the repo (any Cargo.toml in it, so one shared file
serves a repo gated crate by crate) or a file on disk; an entry that fails any of these fails the
run as stale. The file's content is an input of the coverage reuse key. Example `.gatesrc`:
`GOH_COV_FLOOR_RUST=95` and `GOH_COV_FLOORS_JSON=tools/cov_floors.json`.
