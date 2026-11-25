# GOH_LOAD_SOURCES_DIR
Area: runtime
Kind: setting
Order: 30
Default: `$XDG_STATE_HOME/goh/load_sources`, else `~/.local/state/goh/load_sources`

The load-sources ledger (GOH.LOADSOURCES): `sources-<UTC day>.jsonl`, one line per 30 s sample of
cpu-s by process family, repo and executable, written by the launchd job
`com.ztomer.goh-load-sources` (`lib/load_sources.py sample`) and ranked by `goh load --report`; 14
days kept.
