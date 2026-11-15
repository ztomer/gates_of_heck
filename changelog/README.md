# changelog/ -- one file per change

This repository keeps `.gatesrc` GOH_CHANGELOG_FRAGMENTS=on: a change's CHANGELOG entry is its own
file here, never a line under `## Unreleased` in CHANGELOG.md, so two lanes never edit one file
(GOH.CLASS.CHECKOUT part d). The commit gate refuses an entry written under Unreleased by hand.

* Name the file for the change: `<ITEM-ID>.md` (a second change for one item: `<ITEM-ID>-<what>.md`).
* Write top-level `* ` entries only, in the CHANGELOG's own style; no heading.
* The release (`tools/release_goh.sh`, `changelog_cut.py cut --fragments changelog`) folds every
  fragment into the stanza it cuts, in name order, and its commit deletes them.

This README is the directory's documentation and is never folded.
