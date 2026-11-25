# GOH_RELEASE_BACKFILL_FLOOR
Area: release-kit
Kind: setting
Order: 20
Default: unset (no floor)

The oldest tag the release step backfills a GitHub release for (`v2.2.0` or `2.2`): an earlier tag
is neither backfilled nor warned about, in the live loop and the `--dry-run` plan alike. For a repo
whose CHANGELOG deliberately starts at some version (ztools: 56 pre-v2.2.0 tags). Declared in
`.gatesrc` (read by name in a child) or exported; a value that is not a version is refused.
