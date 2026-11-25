# GOH_LAND_PRE_TOOK
Area: pushes
Kind: setting
Order: 190
Default: unset

Where `tools/land.sh` writes its pre-step's figures as one JSON object in every outcome -- `wait_s`,
`run_s`, `limit_s`, `workers`, `cpu_s`, `verdict` (green, none, red) -- for the land record's
`pre_run` (GOH.PREWIDTH). Internal: `goh land --enqueue` names a temp file and reads it back.
