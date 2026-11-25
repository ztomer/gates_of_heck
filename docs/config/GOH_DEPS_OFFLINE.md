# GOH_DEPS_OFFLINE
Area: deps
Kind: setting
Order: 30
Default: unset

`1` skips the crates.io arm and `goh crates-behind` (every Cargo.lock at its newest compatible
releases, `cargo update --dry-run`), and judges `cargo audit` against the cached advisory database
(`--no-fetch`; with no copy anywhere to seed from, the first audit fetches one), and PRINTS each.
Absence of evidence is not a clean bill, and a check that prints "clean" because the index was
unreachable converts a missing measurement into evidence.
