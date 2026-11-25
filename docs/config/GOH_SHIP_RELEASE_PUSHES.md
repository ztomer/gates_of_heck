# GOH_SHIP_RELEASE_PUSHES
Area: ship
Kind: setting
Order: 50
Default: unset

`1`: the release kit pushes the hand-off together with its release commit, so the push step is
skipped (said) whenever the release step runs. For a repo whose gate refuses the hand-off alone (a
private Swift app: a release note may only land in the release commit). A HEAD already tagged still
gets the push step.
