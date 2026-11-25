# GOH_DEPS_RATCHET
Area: deps
Kind: setting
Order: 20
Default: unset

Path to a file of the crates this repo has TRIAGED as a major behind, one per line as `name  # why
it may stand`. Any major-behind NOT listed fails; so does an entry with no reason, and an entry
whose crate is no longer a major behind (or not a dependency at all) -- the set can only shrink.
"Major" is Cargo's compatibility boundary, so `0.3 -> 0.4` counts. Offline, staleness is not judged
and the report says so.
