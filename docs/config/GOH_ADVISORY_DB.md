# GOH_ADVISORY_DB
Area: deps
Kind: setting
Order: 40
Default: `~/.cache/goh/advisory-db`

The advisory database every goh `cargo audit` reads and refreshes (`lib/advisory_db.py`): goh's own
copy, under a read/write flock, so a refresh never rewrites a file another goh audit is reading.
Seeded from `~/.cargo/advisory-db` when missing, then refreshed from the upstream (its `origin` is
set to RustSec's, never the seed). Point it elsewhere only to isolate a test.
