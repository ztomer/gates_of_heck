# GOH_AUDIT_CPU
Area: gate-source
Kind: setting
Order: 20
Default: `30`

CPU seconds one `cargo audit` may burn before `lib/advisory_db.py` stops it and names the stall
(GOH.AUDITSPIN): cargo-audit 0.23.0's yanked check spins in getaddrinfo forever when the index
lookup fails fast (tame-index 0.26.3). A whole 412-crate scan with its fetch costs under 1 s.
