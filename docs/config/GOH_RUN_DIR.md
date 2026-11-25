# GOH_RUN_DIR
Area: deps
Kind: setting
Order: 120
Default: `$XDG_STATE_HOME/goh/run`, else `~/.local/state/goh/run`

env: the root every goh temp, lock and stage path is minted under (`lib/run_path.sh` `goh_run_root`,
`lib/run_path.py`, `crates/goh/src/run_path.rs`), outside every work tree. Each tree gets
`<root>/<basename>-<16 hex of sha256(physical path)>`: `build.lock` (`scripts/build-goh.sh`),
`tree.lock` (`lib/tree_lock.sh`), `release` (the release kit's worktree, one stable path so its
cargo build-dir is reused), and a placement's `<base>.<pid>.staging` (`lib/place.sh`, beside the
destination only when the root is on another filesystem); `index/` holds the private index copies
`goh_index_tree` writes a tree from, so a gate never takes the shared `index.lock`
(GOH.REFRESHINDEXLOCK). A relative value is refused. Tests point it at a temp dir.
