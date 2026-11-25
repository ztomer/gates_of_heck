# GOH_LAND_PRE_ONLY
Area: pushes
Kind: setting
Order: 110
Default: unset

Set to any value, `tools/land.sh` stops right after its pre-step: no land lock, no gate, no
fast-forward, no ledger row. `goh land --enqueue --land` runs it this way in the caller's worktree,
so a red tip is refused before it queues and a pre-step past its deadline queues anyway
(GOH.ENQUEUEPRE). Internal: the queue sets it.
