# GOH_REMOTE_ADVISORY_DB_URL
Area: remote-worker
Kind: setting
Order: 100
Default: `https://github.com/RustSec/advisory-db.git`

Where the worker's advisory database is cloned from (shallow, by a short-lived networked container)
when its copy is absent or a day old: the suite's rust-gate audits read it offline
(GOH.WORKERFAILS). A seam for the tests.
