# GOH_COMPACT_NUDGE_STEP
Area: claude-hooks-2
Kind: setting
Order: 20
Default: `50000`

Growth after a nudge before the next one: one message per step, not one per turn. A compaction
re-arms it, whether the Stop saw the boundary or only the shrink. State is one file per session
under `$XDG_STATE_HOME/goh/compact_nudge/` (default `~/.local/state`), pruned after seven days.
