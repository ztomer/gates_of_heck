* A pathspec commit (`git commit -- <path>`) or `commit -a` passes a gate step that reads the index
  with a plain `git` (GOH.PATHSPECCOMMIT). A private Swift app refused every pathspec commit:
  `index_tree: git write-tree ...: Unable to create .../index.lock: File exists`. git holds the
  repository's index lock while the hook runs and commits a temporary index named only by
  GIT_INDEX_FILE, which the hook drops at entry (contract #12) and carries as GOH_HOOK_INDEX_FILE, so
  a step that did not call `goh_bind_hook_index` (Python here, out of reach of `goh bare-hook-index`,
  which reads shell) wrote the tree of the locked real index. While a hook carries an index,
  `goh_hook_unbind` now puts `lib/hookgit/git` first on PATH: it binds the carried index for an
  index read (`write-tree`, `ls-files`, `diff`, `diff-index`, `diff-files`, `grep`, `show`,
  `cat-file`, `status`, `checkout-index`, `check-attr`) in the committing repository only, never for
  a writer, `init`/`clone`, or a scratch repo, and never over a caller's own GIT_INDEX_FILE. A plain
  commit carries nothing and gets no shim. `tests/test_pathspec_commit_index_reader.py` commits for
  real in a main checkout and a linked worktree; its calibration runs the step through the real git
  by path and gets the lock error back.
