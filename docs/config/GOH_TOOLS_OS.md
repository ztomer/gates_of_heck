# GOH_TOOLS_OS
Area: runtime
Kind: setting
Order: 490
Default: the host (`platform.system()`)

Test seam of `gates/required_tools.py --install`: the OS its install lines are for. `Darwin` prints
`brew install ...`; anything else prints a presence check per brew tool that refuses a missing one
by name, because brew is the macOS route (multitop's ubuntu run, CI.PUBLIC2). Not consumer config.
