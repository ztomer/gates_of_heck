# GOH_SEED_PENDING
Area: runtime
Kind: internal
Order: 300
Default: set by `gates/commit_gate.sh`

The directory a commit's seeded checks journal their seed settles in (GOH.SEEDREFUSED, D-0005); `goh
seed-settle` applies it only after every commit-gate step has passed, and a refused commit's journal
is deleted unapplied; off the store's branch (`origin/HEAD`, else `main`) it is licensed under
`<store>/deferred/` and applied by the first full run on that branch that finds the change
(GOH.SEEDBRANCH). Not a setting.
