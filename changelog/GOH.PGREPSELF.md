* The Claude Bash guard refuses a `pgrep -f` / `pkill -f` that matches the shell running it
  wherever it stands, not only in a `while`/`until` condition (GOH.PGREPSELF): a pattern spelled a
  second time in the same command (`pgrep -f foo || foo &`), or a call inside a `bash -c '...'`
  string or an `ssh host '...'` remote command, whose own shell holds the pattern, so "is it
  running" is always yes and a wait on it never ends (two lane loops, fleet-deploy over ssh and a
  camoufox neighbour kill, 2026-11-15). The refusal names the safe forms (the pid of what you
  started or a pid file, waiting on a log line, the `[f]oo` bracket form); `$$` excludes the own
  pid; `# pgrep-ok: <reason>` is the escape. `lib/self_match.py`, `judge_anywhere`.
