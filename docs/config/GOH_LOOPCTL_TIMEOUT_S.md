# GOH_LOOPCTL_TIMEOUT_S
Area: ship
Kind: setting
Order: 190
Default: `15`

Seconds the per-turn agent-stalls hook waits for `loopctl stale` (`hooks/claude/loop_stale.py`,
GOH.STALEHOOK) before killing it; a slow or failing CLI is said on stderr only.
