* `checks/_gitutil.py local_env_vars` raises on a `git rev-parse --local-env-vars` answer that
  names no GIT_DIR, as it already refused such an inherited GOH_GIT_LOCAL_VARS (GOH.FLAKYGITDIR):
  lib/gitrun.py caches the list for the process, so a test's git shim first on PATH (the compact,
  discard-guard and ship lock tests) answered with nothing, the empty list was cached for its xdist
  worker, and every later `repo=` call there kept an inherited GIT_DIR --
  test_repo_drops_a_leaked_git_dir went red whenever a shim test ran before it on one worker. The
  shims hand that one question to the real git.
* tests/test_landq_look.py's mover files the done record once, its outcome in it, as the lander
  does: it wrote a record with no state and then the outcome, and a wait that read the gap exited 1
  ("T.S: None @ aaaaaaaa"). Each done record is now held 0.3 s in a second case, which turned the
  old two-write mover red every run.
