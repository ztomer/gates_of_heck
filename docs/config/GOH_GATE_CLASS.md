# GOH_GATE_CLASS
Area: pushes
Kind: internal
Order: 420
Default: `work`

Which lane and rank a gate is admitted by (GOH.SLOTFAIR, GOH.SLOTPRIORITY, `lib/gate_lanes.py`
CLASSES): `short` -- the commit gate's own lane of `GOH_GATE_SLOTS` slots (`short-<i>`), which no
long gate takes, so a `gate.sh --staged` never waits behind a push, a land or an agent's `load.py 0
-- cargo test` (2026-11-16: a commit gate waited 38+ min behind a 49-min test run and a 15-min push
gate); `pre` -- a land's pre-step and the lander's pre-land (`tools/land.sh`), in the same short
lane at the same rank, named apart because it is the land path (GOH.LANDSHARE: with `land` and
`ship` it takes `GOH_LAND_SHARE` of the cores and runs at default QoS). In the long lane, by rank:
`land` (a land's gate), `ship` (`goh ship`'s suite and push), `lane` (a gate run in a loop lane's
worktree: derived, never declared -- a checkout under the lanes root, `$LOOPCTL_LANES_ROOT`, else
`$XDG_STATE_HOME/loop/lanes`, else `~/.local/state/loop/lanes`, is `lane` unless its caller declared
another class, GOH.LANECLASS; a lane's `tools/pytest.sh` whole run, `land --check` and push gates),
`work` -- the default and the lowest: the pin sweep's consumer pushes, every push gate nobody
classed (another repo's, the coordinator's), a hand-run `gate.sh --full`. Until 2026-11-21 every
push gate was `ship`, ranked with a land: a land's gate waited 161 s behind the coordinator's push
gate and a consumer lane's `ci_hook.sh`, earlier lands 293 s behind the sweep. Within a rank a freed
slot goes to the longest live waiter, and a waiter past `GOH_GATE_SLOT_STARVE_MIN` goes ahead of
every class: each waiter holds a flock'd ticket in `<GOH_GATE_SLOT_DIR>/waiting/` naming its rank,
arrival and promotion time, and only the first live ticket takes a slot; a dead waiter's ticket is
unlocked and reaped. Declared by the callers (or, for `lane`, derived from the checkout), never read
off a command line -- `gates/commit_gate.sh` (`short`, for the gate.sh it runs), `tools/land.sh`
(`pre` for its pre-step and pre-land), `lib/land_gate.sh` (`land`), `tools/ship.py` and
`lib/ship_full_run.py` (`ship`) -- and consumed by the acquire, so nothing a gate starts inherits
it. The wait is recorded per class: a push row's `slot_class`, a land record's and its `land
attempt` row's `slot_wait_by_class`, shown by `goh land-profile` as `slot wait <class>` under `slot
wait`. Any other word is exit 2. Not a setting.
