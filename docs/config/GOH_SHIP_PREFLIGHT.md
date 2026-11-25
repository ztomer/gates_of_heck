# GOH_SHIP_PREFLIGHT
Area: ship
Kind: setting
Order: 40
Default: unset

The repo's release preflight, `{version}` substituted (`tools/ship_release.sh --check {version}`):
run by `goh ship` BEFORE its first step whenever the release step will run, in the repo, with
`GOH_SHIP_VERSION`, `GOH_SHIP_SUMMARY` and `GOH_SHIP_ITEM` exported, git's bindings unset and
`GOH_SHIP_TIMEOUT`'s bound. A non-zero exit refuses the ship with its last lines and nothing is
pushed (GOH.SHIPPREFLIGHT). goh's own changelog reading (`changelog_cut.py ready`) runs first
through the same path when CHANGELOG.md exists. Every precondition the kit checks that is knowable
before the push belongs here; `scripts/ship_preflight_audit.py` lists the estate's kits that declare
none.
