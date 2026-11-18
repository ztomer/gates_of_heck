* `goh budget` judges a goh land END TO END (GOH.LANDTARGET, owner target 2026-11-18): two subjects,
  `land e2e plain` (ARM 5 min, BREACH 7.5 min) and `land e2e instrumented` (the land ran the
  coverage step; ARM 10, BREACH 15; the breach lines are the owner's: "yes, 7.5 and 15 for the
  breach"), each on the median of its last five runs, named constants in lib/budget.py. lib/land_e2e.py writes one row per landed item from the landq records: its first
  request (taken before the enqueue's pre-step; older records start at their filing, said as
  `from: filed`) to main fast-forwarded, its retries inside it; a batch of N is N rows sharing one
  gate. The relative guard judges the land gate's cpu-s, never the wall. The done record now keeps
  the request time and the pre-step's seconds.
* `goh budget --json` carries `breaches`: every BREACHED subject with the self-improvement item it
  calls for, and the table prints each, so a breach reaches the coordinator instead of sitting in a
  row.
