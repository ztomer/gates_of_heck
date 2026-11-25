# GOH_TUI_BOOTSTRAP
Area: runtime
Kind: setting
Order: 510
Default: unset

Set (never exported) by a script that runs BEFORE the house Python exists --
`.github/actions/goh-floors/floors.sh`, which installs it: `tui/lib.sh` then loads its own copy
instead of the trampoline to HEAD's export, which sources `lib/house_python.sh` and refuses a
machine below the python3 floor (G7: every GitHub runner). Not consumer config.
