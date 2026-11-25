# GOH_SHIP_VERSION
Area: ship
Kind: setting
Order: 200
Default: set by `goh ship`

Exported to every step: the version the release step cuts (`--version`, `--bump`, else `git-cliff
--bumped-version` over the conventional commits since the newest tag, a patch when it finds nothing
to bump; no git-cliff refuses the ship, naming `brew install git-cliff`). Never set by hand. The
result's `sha` is the LANDED commit (HEAD after the release step, which may commit a bump);
`handoff_sha` is the commit handed off.
