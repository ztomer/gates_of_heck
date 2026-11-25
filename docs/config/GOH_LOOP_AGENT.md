# GOH_LOOP_AGENT
Area: runtime
Kind: internal
Order: 80
Default: unset (the main thread, the owner)

Not a setting: the agent stamp the Bash guard (`hooks/claude/discard_guard.py`, `lib/commit_hold.py
stamp`) puts at the head of every subagent's command (`export GOH_LOOP_AGENT=<agent id>`), inherited
by every process the shell starts, never a verdict input. `gates/hook.sh`'s pre-commit refuses a
commit whose agent is not the holder of a repo an in-progress queue item holds (GOH.HOLDSUBPROC);
`goh undo` names the caller by it in the edit ledger (GOH.UNDOOWNER).
