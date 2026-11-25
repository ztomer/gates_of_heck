# GOH_CHANGE_BASE
Area: deps
Kind: setting
Order: 200
Default: env, set by `push_gate.sh`

The commit the change being gated started from: the remote's tip of the pushed ref, or for a new
branch its merge base with the remote's default branch. `goh crates-behind` reds only a behind lock
entry absent from that commit's lock; unset, it takes the merge base with the upstream, else HEAD's
parent (docs/decisions/D-0001-crates-behind-judges-the-change.md).
