# GOH_LAND_SHARE
Area: pushes
Kind: setting
Order: 430
Default: `0.75` (goh's `.gatesrc` names it)

The land path's fraction of the cores (GOH.LANDSHARE, `lib/land_share.py`): while a `pre`, `land` or
`ship` gate is admitted, those runs split this part of the cores and every other gate (lane, work, a
commit gate) splits the rest, at least 1 each; with none admitted every gate gets `cores //
admitted` (GOH.POOLSHARE). Read at admission from the environment, else the repository's `.gatesrc`,
else 0.75, and handed to every runner as `GOH_SHARE_LAND`, with the gate's class as
`GOH_SHARE_CLASS`; the live token pool (`lib/worker_share.py` `live`) re-reads the held slots'
classes (the slot record's fifth field) so a lane run narrows within one test of a land's pre-step
starting and widens when it ends. The shares sum to at most the cores (THE BOUND). Equal shares gave
a pre-step 5 of 16 workers beside lane gates (2026-11-21: 969 s at 5 workers, then the lander ran
the same set again at 5). Anything but a number in (0, 1] refuses the admission.
