# GOH_LOG
Area: runtime
Kind: internal
Default: set by `goh_init`

The temp file a step's output goes to, removed when the gate exits. Every gate that sources
`gates/_common.sh` sets it before anything reads it. Not a setting.
