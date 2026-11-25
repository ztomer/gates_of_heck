# GOH_RUN_PATHS
Area: structural
Kind: setting
Order: 250
Default: unset (check skipped)

Set to `on` to run `goh run-paths` (`crates/goh/src/runpaths.rs`, GOH.CLASS.CHECKOUT part 2): a
temp, lock or stage name (`.lock`, `.staging`, `.tmp`, `XXXXXX`, `mktemp`, `mkstemp`, `mkdtemp`,
`NamedTemporaryFile`, `TemporaryDirectory`) on a line that roots it at a work tree (`$PROJECT_ROOT`,
`$repo`, `$root`, `$top`, `$main`, `$wt`, `$PWD`, a positional `$1`..`$9`; Python `repo /`,
`Path.cwd()`, `dir=repo`; Rust `repo.join(`), a path in such a root's `build/`, or a `git
write-tree` (it writes the shared index under `index.lock`) in a shell, Python or Rust source is
red. The one place these are minted is the run-path helper (`lib/run_path.sh`, `lib/run_path.py`,
`crates/goh/src/run_path.rs`), which the scan does not read; tests, comment lines and a Rust file's
`#[cfg(test)]` tail are not read either. Escape: `run-path-ok: <reason>` on the line or the line
above. The sites standing when it landed are seeded in `run_paths_seed.json` (`crate::estate_seed`);
it landed empty. goh's own `.gatesrc` sets it; the shapes are goh's, so it is opt-in.
