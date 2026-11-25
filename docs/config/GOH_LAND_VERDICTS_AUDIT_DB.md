# GOH_LAND_VERDICTS_AUDIT_DB
Area: pushes
Kind: internal
Order: 300
Default: unset

Not a setting: the goh.db the caller's reuse audit counts into (`lib/reuse_audit_store.py`: beside a
named `GOH_REUSE_DIR`, else `GOH_DB`, else `$XDG_STATE_HOME/goh/goh.db`), resolved by
`tests/suite_home.py` beside `GOH_LAND_VERDICTS_STORE`; the controller opens it for the call only
(`reuse_audit_store.NAMED`), the one real-home goh.db a suite's audit may write.
