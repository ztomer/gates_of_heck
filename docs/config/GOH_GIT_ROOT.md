# GOH_GIT_ROOT
Area: runtime
Kind: internal
Default: `git rev-parse --show-toplevel`

The top of the gated repo, empty outside one: where the working tree is stamped and a repo's own
`tui/lib.sh` is looked for. Every gate that sources `gates/_common.sh` sets it before anything reads
it. Not a setting.
