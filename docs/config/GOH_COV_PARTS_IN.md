# GOH_COV_PARTS_IN
Area: coverage
Kind: setting
Order: 70
Default: unset

rust only, hosted CI's merge job: build and run nothing, merge the parts in this directory
(relative: to the project) and judge them as one run (floor, per-crate summary, diff coverage); red
unless `part-workspace.info` and every `part-external-K-of-N.info` of one N carry their `.ok`
markers. With `GOH_RUST_GROUPS=coverage` only.
