# Ship (`goh ship`, `tools/ship.py`, GOH.SHIP)
Order: 180
Level: 2

The local landing pipeline an agent hands off to (private repos; public ones keep standard GitHub
workflows, CI.PUBLIC): push through the pre-push gate -> the repo's release kit -> install -> the
`loopctl verify` card -> `loopctl done ID --shipped RESULT`, detached and bounded, one result JSON.
On a GitHub remote the verify step first reads HEAD's CI verdict ONCE through `loopctl ci SHA --repo
OWNER/NAME`, never waiting: hosted CI is advisory (owner 2026-11-11, .loop LOOP.CIADVISORY,
GOH.CIWAIT). Red or unread fails the ship at verify; queued, running or throttled passes as advisory
when the checkout's pre-push hook is goh's push gate (`loopctl done` records the run, `loopctl tick`
re-reads it and names a red one), and fails at once without one. `--bump none` ships with no release:
a repo with no v-tag, or a HEAD already tagged; past the newest tag it is refused, naming
`--version X.Y.Z`.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
