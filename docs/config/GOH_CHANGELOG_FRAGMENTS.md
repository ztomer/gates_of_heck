# GOH_CHANGELOG_FRAGMENTS
Area: structural
Kind: setting
Order: 300
Default: unset (off)

`on` keeps each change's changelog entry in its own file, `changelog/<ITEM-ID>.md` beside the
changelog (top-level `* ` entries, no heading; the directory's README.md is no fragment), so two
lanes never edit one file (GOH.CLASS.CHECKOUT part d: every lane land of 2026-11-14 conflicted on
the one Unreleased stanza). The structural step `no changelog entry under Unreleased`
(`crates/goh/src/changelog_fragments.rs`) refuses a commit whose changelog holds a line under an
Unreleased heading; the release cut leaves it empty, so a release commit passes the same rule.
`changelog_cut.py cut|ready --fragments DIR` folds the fragments into Unreleased in name order,
`check --fragments DIR` refuses one left at the tagged commit, and the release commit deletes them:
`tools/release_goh.sh`, the kit's step 0b (`release_fragments`, `version_step.sh`) and `goh ship`'s
changelog reading (`lib/ship_changelog.py`) each read the key. `off` or unset leaves the stanza as
the repo writes it; any other value is refused naming the key.
