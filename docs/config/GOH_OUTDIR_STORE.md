# GOH_OUTDIR_STORE
Area: rust
Kind: setting
Order: 70
Default: `$XDG_CACHE_HOME/goh/outdir` (`~/.cache/goh/outdir`)

Where `lib/goh-rustc` keeps the content-addressed copies of build scripts' `OUT_DIR`s
(GOH.SCCACHEPATHS, F3): a compile with `OUT_DIR` set gets `<store>/<hash of every file's path and
bytes>` instead of the build dir's path, which sccache 0.19 hashed into the crate's key and its
dependents'. A copy that fails leaves `OUT_DIR` as cargo set it. Nothing evicts the store yet
(GOH.CACHEEVICT's evictor).
