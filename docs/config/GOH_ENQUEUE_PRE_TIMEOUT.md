# GOH_ENQUEUE_PRE_TIMEOUT
Area: pushes
Kind: setting
Order: 230
Default: `1800`

Seconds `goh land --enqueue --land` waits for its own pre-step run (the slot wait plus
`GOH_LAND_PRE_MAX`) before killing it; a kill is a refusal (GOH.ENQUEUEPRE).
