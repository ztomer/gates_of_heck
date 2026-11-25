# GOH_COMPACT_NUDGE_AT
Area: claude-hooks-2
Kind: setting
Order: 10
Default: `300000`

Tokens of main-conversation context past which `compact_nudge.py` (Stop) tells the owner, at the end
of a turn, that `/compact` would land on a task boundary. Set it under the `autoCompactWindow`, or
the automatic compaction comes first and the nudge never speaks.
