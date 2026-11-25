# GOH_SEED_DIR
Area: deps
Kind: setting
Order: 110
Default: `$XDG_STATE_HOME/goh/seeds`, else `~/.local/state/goh/seeds`

env, `lib/cip_history.py` (`cip_history.json`, the CIP lanes' push gates `goh budget` backfills,
GOH.GATEBUDGET2), every estate-seeded check (`crates/goh/src/estate_seed.rs`: changelog-tags,
mktemp-portable, bare-open, release-quiet, bare-hook-index; `scripts/release_canary.py` passes it to
every side's scanners, so a lane stages a seed in a store of its own, GOH.CANARYSEEDDIR): the
owner's PRIVATE store of seed entries, in no repository's tree, keyed by root commit. A repository's
entries live there or in its own tracked `<kind>_seed.json`, never in another repository's tree: a
tracked entry naming another root is refused, one entry in both homes is refused (GOH.SEEDPRIVATE).
A store entry for a repository whose CI runs goh (a workflow that checks out, clones or uses
`<owner>/gates_of_heck`) is refused naming the repository, since a runner cannot read the store: its
entries belong in its own tracked file (GOH.STORECI). The refusal is judged with the findings: a
full run that finds such an entry's file clean retires it from the store and says so, and a commit's
run names that full run, `goh structural --full` (GOH.STORECISTALE).
