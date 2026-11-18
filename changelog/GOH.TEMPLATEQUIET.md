# GOH.TEMPLATEQUIET

Every fixture template builds with git's background work off. `tests/_tree_clone.py` runs each
`template()` / `process_template()` / `from_template()` build under an XDG git config with
`maintenance.auto=false` and `gc.auto=0` (read by every git the build spawns, which
`hermetic_env(drop_git=True)` and `_fast_git` cannot drop), then waits until no `maintenance.lock`
or `gc.pid` is held in the tree before it scans or clones it. The two hand-set
`maintenance.auto` fixes (`conftest.py` `_repo_template`, `_shipped_kit.py`) are gone: the first now
builds through `template`; the second already did. Cause: the ship of v0.136.0 died on
`FileNotFoundError .git/objects/maintenance.lock` in `test_claude_discard_guard.py`.
