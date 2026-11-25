# GOH_COV_CACHE
Area: gate-source
Kind: setting
Order: 50
Default: `~/.cache/goh/llvm-cov`

Where the rust coverage BUILD dir lives, one directory per REPO (`<sha256 of the repo root's
physical path>/build`, `/external`; a crate outside git keys on its own path), shared by the repo's
crates (GOH.COVSHAREDBUILD; each report reads a view of its own build's executables), so a push
gate's worktree, deleted after every push and recreated at the same path, reuses the instrumented
build of its dependencies. The target dir stays in the tree (`target/llvm-cov`): its binaries may
locate their repo from their own path. Pruned like the HEAD exports: a week unused, past the newest
200, or past `GOH_COV_CACHE_MAX_GIB` -- each only once idle: no live `.goh-running` pid, and its
marker lock free (`_head_cache._evict` decides and renames the entry away under the lock `mark`
takes); an entry with no marker at all is also spared for 10 minutes after its stamp. A run's
scratch -- its `.profraw` profiles and the report's `cov-view` links -- is deleted when the run
exits, pass or fail; its `lcov-parts` record stays (EST.FSEVENTSCHURN).
