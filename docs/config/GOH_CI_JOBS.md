# GOH_CI_JOBS
Area: local-ci
Kind: setting
Order: 100
Default: **1** (serial)

How many `GOH_CI_STEPS` run at once. Reports still come out in DECLARED order and the fail
accumulator is unchanged. Steps prefixed with resource tags, `[db,screen] cmd`, never overlap a step
sharing a tag (tags: letters, digits, `_.=/-`; no colon, which separates steps -- write
`cargo=crates/x`). Serial by default because only the repo knows which of its steps write the same
file. Non-numeric or `0` is an exit-2 config error.
