# GOH_FULL
Area: runtime
Kind: internal
Order: 70
Default: unset (a summary)

Not a setting: the output switch a lane-facing command's `--full` exports for its children, never a
verdict input. `1`: `goh land --wait`, `goh land --check` and the foreground enqueue pre-step print
a red's whole log, not its summary of under 30 lines (lib/terse.py, GOH.TERSEOUT).
