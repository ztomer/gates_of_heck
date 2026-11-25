# GOH_HOOK_GIT_DIR
Area: runtime
Kind: internal
Order: 280
Default: set by `goh_hook_unbind` (`gates/_git_env.sh`)

The git dir `GOH_HOOK_INDEX_FILE` belongs to: the index is bound only inside that repository, never
in a fixture a test runs a gate on. Not a setting.
