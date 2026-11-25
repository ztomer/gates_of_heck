# GOH_LIVE_ROOT
Area: gate-source
Kind: internal
Order: 100
Default: internal (Not a setting)

Set by `_from_head.sh` for the export's children: the live checkout, where `bin/goh` and its
stamp-keyed store `bin/by-stamp/` (not in git) are found. Honoured only for an export
(`lib/goh_root.sh` `goh_live_root_for`): a plain checkout that inherits it ignores it. Not user
config.
