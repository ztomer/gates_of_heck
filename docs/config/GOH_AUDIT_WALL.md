# GOH_AUDIT_WALL
Area: gate-source
Kind: setting
Order: 30
Default: `360`

Wall seconds one `cargo audit` may run (it may wait up to 300 s for cargo's package lock, which
costs no CPU).
