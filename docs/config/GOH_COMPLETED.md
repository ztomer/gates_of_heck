# GOH_COMPLETED
Area: runtime
Kind: internal
Default: set by `goh_done`

`1` once the gate reached `goh_done` (`gates/_common.sh`): the EXIT trap honours a zero exit only
then. Every gate that sources `gates/_common.sh` sets it before anything reads it. Not a setting.
