# GOH_REUSE_DIR
Area: verdict-store
Kind: setting
Order: 30
Default: `$XDG_STATE_HOME/goh/reuse`, else `~/.local/state/goh/reuse`

The store's root (the profile store, the audit, and the verdict records from before GOH.VERDICTDB;
named, its `goh-cache.db` holds the records); a relative path is refused.
