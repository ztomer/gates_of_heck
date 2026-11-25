# GOH_ITEM
Area: ship
Kind: setting
Order: 140
Default: unset

The queue item the caller works for, read by `goh undo` (`tools/undo.py`, GOH.UNDOMINE) when no
`--item` is given: a worktree a landing-queue record or loop lane claim gives to another item is
refused. Unset, no item is compared. The same item (and `GOH_LOOP_AGENT`, the agent stamp on a
subagent's shell) is the caller `goh undo` checks the worktree's edit ledger (`<git
dir>/goh-edits.jsonl`, GOH.UNDOOWNER) against: a file another item or agent wrote after the caller's
own last write is refused.
