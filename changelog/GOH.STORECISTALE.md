* A stale private-store entry for a repository whose CI runs goh retires itself instead of being
  refused (GOH.STORECISTALE). GOH.STORECI refused such entries at load, before any finding was
  read, so in token-pool (2026-11-16) it fired on entries whose sites were already fixed, said only
  "move it into the tracked seed", and the lane hand-deleted them from the store. The refusal is now
  judged with the findings: a full run that finds the entry's file clean deletes it from the store
  under its lock (the retirement path GOH.SEEDPRUNE writes through, now shared as
  `estate_seed::retire_store`) and says `retired`; an entry still holding a site is refused as
  before; a commit's run, which cannot tell, names the full run that retires it,
  `goh structural --full`.
