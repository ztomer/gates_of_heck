# Runtime / harness
Order: 220
Level: 2

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->

Internal-only (not `.gatesrc` policy): `GOH_ROOT`, `GOH_GIT_ROOT`,
`GOH_REPO_ROOT`, `GOH_NAME`, `GOH_LOG`, `GOH_LOGS`, `GOH_COMPLETED`, `GOH_EX`, `GOH_NATIVE_BIN` (the resolved native binary inside `structural.sh`), `GOH_TMPDIRS` (temp dirs the EXIT trap removes), `GOH_INDEX_VIEW` (the index export `goh_index_view` hands a whole-tree checker at `--staged`).
