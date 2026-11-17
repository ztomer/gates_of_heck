* Proven steps are reused from one goh land to the next (GOH.PROVENPYPATH). Every proven key held
  `env PYTHONPATH=<path>`, and goh's land puts the tip's head export (`~/.cache/goh/head/<sha>`, new
  per commit) first on PYTHONPATH, so every group (crate, repo, structural, ...) was keyed by that
  path and none was ever found again. PYTHONPATH now joins the run-constant identity by content,
  through the one formula the Rust coverage key already used (`lib/gate_identity.py` `pythonpath`):
  each gates root on it by the gates' files (the coverage key: its coverage scripts), a root behind
  another only by the files the one ahead lacks, any other entry by its path; a changed file a gate
  imports still moves the key. The estate-corpus cache had the same hole twice: it keyed GOH_DIR's
  path and the export's `bin/goh` launcher (a fresh copy, new mtime, in every export); it keys the
  gates there by content now.
* The land after GOH.RUSTCOVKEYSCOPE's (GOH.VERDICTFREEINLINE, tests/docs/changelog and
  `gates/verdict_free_keys.txt`) still re-measured Rust coverage (830 s), naming `docs/` and
  `changelog/` as what moved in PYTHONPATH. Neither had: its PYTHONPATH held one more copy of the
  same gates, keyed as one more `goh:shadowed`, and a key line that names no files made the miss
  run a bare `git diff --` over the whole tree. A wholly shadowed gates root and a repeated entry are
  no input now, and such a line names no files.
