* GOH.PYHARNESS: a test run pays less before its first test. pytest's rewritten test files are kept
  between runs, in one store every worktree shares (`tests/_rewrite_store.py`): the agent shells
  export `PYTHONDONTWRITEBYTECODE=1`, so every run and every xdist worker rewrote 809 files again.
  `--collect-only` of the suite took 11.5 CPU seconds and takes 3.6.
* GOH.PYHARNESS: the resolve of the goh the gates run (`tests/_prebuild.py`) is started beside the
  run and joined before its first test, in place of a 0.3-0.5 s wait ahead of collection; a
  `--collect-only` starts none.
* GOH.PYHARNESS: `tools/pytest.sh` asks what the suite refuses to start without in one process
  (`lib/suite_preflight.py`) in place of eleven: 0.3 s for 1.9 s. Every module is still imported
  (a broken one is refused with its error, a crashing one by name), each pin and every ruff held,
  and a refused run leaves no temp root. The pins are the tree's own manifest.
* GOH.PYHARNESS: a run narrowed to files or node ids takes the workers its tests cost
  (`lib/suite_width.py`, the suite's duration table): `-n 0` for 2 s or less, one worker per 1.5 s
  after it; a caller's own `-n 0` (every `goh land --check` job) starts no share pool. A run it
  cannot cost, a land's and a whole one are at the ceiling; `-n` still wins.
* GOH.PYHARNESS: a finished suite's mark (`goh-suite.live`, never removed) no longer costs every gate
  and helper in its checkout a `ps` of every process on the machine (`gates/_from_head.sh`).
* GOH.PYHARNESS: the mean of the bytes a test leaves written is judged without the files the ceiling names for bytes, which hold their own: a pre-step that drew three of them read 76,138 a test and went red. The ceiling falls from 65,000 to 52,000.
