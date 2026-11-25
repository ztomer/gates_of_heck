# GOH_STATE_ROOT, GOH_REPOS_ROOT
Area: pushes
Kind: setting
Order: 313
Default: config.toml's `state_root` and `repos_root`, else `$XDG_STATE_HOME/goh` (`~/.local/state/goh`) and `~/Projects`

Where goh's state and the checkouts are (`lib/state_paths.py`, `crates/goh/src/goh_config.rs`,
GOH.REPOKEY): each absolute or `~/..`; anything else, like a config.toml that does not parse, is
refused by name, never a silent default. Every seam below them (`GOH_DB`, `GOH_SHIP_DIR`,
`GOH_LANDQ_DIR`, `GOH_SEED_DIR`, `GOH_LOOPCTL`, `GOH_ESTATE_ROOT`) keeps its name and takes its
default from them. The file is `$XDG_CONFIG_HOME/goh/config.toml` (`~/.config`); the environment
wins over it. A stored path may be a token at its start (`$STATE/..`, `$REPOS/..`, `$HOMEDIR/..`)
and a stored repo its key under the repos root (`~/..` outside): every reader takes both forms.
