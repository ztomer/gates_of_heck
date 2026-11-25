# GOH_AUDIT_INDEX_HOST
Area: gate-source
Kind: setting
Order: 40
Default: `index.crates.io`

The name a fetching audit resolves first, and refuses when it does not resolve (the proxy's host
wins when `HTTPS_PROXY`/`ALL_PROXY` is set). A test seam; not something to set.
