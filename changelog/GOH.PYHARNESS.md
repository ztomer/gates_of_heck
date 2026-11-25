* GOH.PYHARNESS: a test run pays less before its first test. pytest's rewritten test files are kept
  between runs, in one store every worktree shares (`tests/_rewrite_store.py`): the agent shells
  export `PYTHONDONTWRITEBYTECODE=1`, so every run and every xdist worker rewrote 809 files again.
  `--collect-only` of the suite took 11.5 CPU seconds and takes 3.6.
