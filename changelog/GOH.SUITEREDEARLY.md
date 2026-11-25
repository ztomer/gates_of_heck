* **A ship's uncovered lines are named by its preflight where a run has measured them, and its reds
  are read whole and re-run before they are bisected (GOH.SUITEREDEARLY).** The idle lander's run,
  green or red, now leaves its per-line coverage where the ship's does (`lib/cov_risk.py`
  `keep_run`), and `goh ship` refuses before its suite on every changed line the newest complete run
  measured uncovered (`lib/cov_newest.py`), whether a land carried it or not; the changed lines no
  run has measured are named there and refuse nothing. A carried line is cleared only by that
  measurement: the land of the item filed for it, and a later land's list without it, no longer
  clear it. A run with an unfinished part (a red external suite) is not kept as the profile.
  The red's log: uncovered rows at any indent, test ids with spaces whole, a Rust red beside
  pytest's probed by the full run, and a test the probed land does not have is no red of that land
  (pytest ran none of the ids and the bisect ended). The tip is re-run and red again before any
  older land is probed, and the red is attributed at the commit the suite ran on, not at whatever
  main is once the land lock is given back.
