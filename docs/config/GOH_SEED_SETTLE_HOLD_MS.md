# GOH_SEED_SETTLE_HOLD_MS
Area: deps
Kind: setting
Order: 130
Default: unset

env, test seam only (`crates/goh/src/estate_seed/settle.rs`): milliseconds a commit's store settle
holds between its read and its rename, under the store lock, so `tests/test_seed_scope.py`'s
two-process test races on every run (GOH.SEEDSCOPE).
