# GOH_SHIP_CHANGELOG
Area: ship
Kind: setting
Order: 20
Default: unset

The CHANGELOG.md contract the release kit holds, which `goh ship` asks BEFORE its first step
(GOH.SHIPCHANGELOG): `cut` (goh's `changelog_cut.py`: an Unreleased entry or a written `## vX`
stanza), `unreleased` (the kit renames its ONE non-empty `## Unreleased`, or tags a written `##
vX`), `kit` (the kit cuts its own; its GOH_SHIP_PREFLIGHT asks what it needs). The changelog is
`CHANGELOG.md`, else `docs/CHANGELOG.md` (the locations `goh changelog-tags` reads,
`lib/ship_changelog.py` LOCATIONS; GOH.SHIPCLDOCS), and the reading is asked of that file. Required
when the repo has either and a release runs; unset or unknown is refused with nothing started, since
a guessed contract passed token-pool's ship and its kit refused after the push.
