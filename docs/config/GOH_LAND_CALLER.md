# GOH_LAND_CALLER, GOH_MAIN_COMMIT_OK
Area: commit-messages
Kind: setting
Order: 40
Default: unset

The two keys through the commit refusal above, set per command and never in `.gatesrc`:
`GOH_LAND_CALLER=land.sh` is `tools/land.sh` naming itself to the merge it makes (any other value is
not it); `GOH_MAIN_COMMIT_OK=<reason>` is the human escape, the reason required; each use is
appended (UTC time, HEAD, reason) to `goh-main-commits.log` in the common git dir and said on stderr
(GOH.MAINCOMMIT: kept for the owner's own hand commits, logged so a habit of it shows).
