# GOH_AGENT_STALL_MINUTES
Area: claude-hooks
Kind: setting
Default: `20`

Minutes a subagent's transcript may be still before `hooks/claude/agent_stalls.py` (and `goh
agent-stalls` with no `--minutes`) calls it stalled, and `hooks/claude/stdin_hangs.py` hung. A value
that is no number is the default.
