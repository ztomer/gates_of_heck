# GOH_PUSH_LOGS
Area: structural
Kind: setting
Order: 70
Default: `~/.cache/goh/push-logs`

Where `push_gate.sh` tees each run's output. A green run deletes its log; a red one keeps it and
prints the path (newest 20 kept), so a refused push never loses its evidence.
