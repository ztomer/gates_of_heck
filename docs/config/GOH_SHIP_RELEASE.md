# GOH_SHIP_RELEASE
Area: ship
Kind: setting
Order: 10
Default: unset

The repo's release command, run with `GOH_SHIP_VERSION` exported and `{version}` substituted
(`tools/release.sh {version}`). Required when the repo tags releases (a `vX.Y.Z` tag exists): `goh
ship` refuses before starting without it. Skipped when HEAD already carries a version tag. The
environment (even empty) beats `.gatesrc`.
