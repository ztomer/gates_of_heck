# GOH_LAND_PRE_CARRY
Area: pushes
Kind: setting
Order: 180
Default: unset

Under `GOH_LAND_PRE_ONLY`, the green verdict an item's land red only on a stack conflict kept for
its next enqueue (`<landq>/pre-carry/<item>`, `lib/land_prestep.py` `carried`): read as
`GOH_LAND_PRE_FROM` is, once, so a clean rebase's pre-step reruns only what `lib/pre_reuse.py` says
the base move reaches (GOH.CONFLICTKEEPVERDICT). Internal: the queue sets it.
