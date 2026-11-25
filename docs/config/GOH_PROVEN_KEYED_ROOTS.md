# GOH_PROVEN_KEYED_ROOTS
Area: runtime
Kind: internal
Order: 380
Default: set by `gates/_rust_proven.sh`

The paths whose CONTENT the proven identity carries (each gates root's gates and the goh binary's
build inputs, `lib/gate_identity.py` PARTS, in a checkout, a head export or a plain directory
alike), `:`-separated, from `proven_keyed_roots`. `goh rust-scope` leaves a path dependency inside
one to the identity instead of refusing the scope (antiknob's `goh-tui`, R3.13). Not a setting.
