# GOH_APPSTATE_RUNNING_CMD
Area: claude-hooks
Kind: setting
Default: `ps -axww -o comm=`

A test seam of `hooks/claude/appstate.py`: the command that prints running executables' paths, one
per line, run as `GOH_APPSTATE_ITEMS_CMD` is. Under goh's suite there is no default.
