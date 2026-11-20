* **The consumers' pin sweep runs one gate at a time, and one sweep at a time:** sampled each second for 10 min
  at load 30-70 on 16 cores, a release's sweep was 5000-10300 CPU-s of consumer push gates (54823 s across the
  day's nine sweeps) run as many at once as the machine had gate slots, so background propagation held every long
  slot beside the land and the lanes. `lib/pin_sweep.py` now pushes one consumer at a time, registers every sweep
  (`pin_sweeps.json` beside the pin ledger) until it is done, leaves each repo a newer sweep lists to that sweep
  (`superseded`, naming it: its push carries the older pin commit), and starts a newer sweep's gates only once the
  older has ended. The release rate now sets how often a consumer gate runs, never how many run at once.
