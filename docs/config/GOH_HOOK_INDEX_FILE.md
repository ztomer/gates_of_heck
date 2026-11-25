# GOH_HOOK_INDEX_FILE
Area: runtime
Kind: internal
Order: 270
Default: set by `goh_hook_unbind` (`gates/_git_env.sh`)

The index a commit hook was handed when it is NOT the repository's own (`commit -a`, `commit
<paths>`), carried past the hook dropping `GIT_INDEX_FILE` so the staged scope still judges the tree
being committed (`goh_bind_hook_index`). Not a setting.
