* GOH_LIVE_ROOT is an export's, by ONE rule every reader asks (GOH.LIVEROOTLEAK): `lib/goh_root.sh`
  (`goh_is_export`, `goh_live_root_for`, `goh_head_cache`), `lib/goh_root.py` and
  `crates/goh/src/goh_dir.rs` agree that a directory is an export when it holds `.goh-head` or, pruned,
  sat in the head cache. The dead-GOH_DIR repair (shell, Python, native), `gitutil::root_commit`, the
  binary resolver, an export's `bin/goh` and `gates/hook.sh` (after the stock shim) honour
  GOH_LIVE_ROOT only for one: a GOH_DIR naming a removed lane worktree no longer takes the live root
  an ancestor's export leaked. The suite's entry (`tests/suite_home.py`) drops EVERY GOH_* its caller
  set but the gate's declared inputs (`CALLER_INPUTS`) and points GOH_DIR at its own checkout; the
  release-kit tests build their children's env with `hermetic_env`.
