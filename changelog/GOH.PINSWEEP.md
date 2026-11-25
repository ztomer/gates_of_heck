* The consumers' pin pushes run once a day, not once a release (GOH.PINSWEEP): `goh ship`'s install
  still commits each pin, and the sweep of their full push gates waits until no release followed for
  `GOH_PIN_SWEEP_QUIET` (6 h) or `GOH_PIN_SWEEP_EVERY` (24 h) has passed since the last; a newer
  release's sweep takes the waiting one's repos. Replayed over the 62 sweeps of 2026-11-16..23: 10
  run, 28.95 h of a gate slot is 4.34 h.
* A repo whose gate refused this same tip, and of whose failed steps goh moved nothing one reads, is
  held: named, not pushed (`lib/push_hold.py`). A refusal's reason is the failed step and its fault
  lines, not the log's last three (`lib/push_refusal.py`); a push no gate judged says so. The pin waiting longest is pushed
  first.
* A push sweep's record, its outcomes and each turn's wait for a gate slot are goh.db rows (0022,
  `lib/push_sweep_rows.py`); `goh state retire sweeps` imports the `.sweep.json` files and archives
  them. A sweep whose supervisor died is ended as failed at the next ship, sweep or listing.
* `goh wf-pin --owners` names each consumer an owner's unpushed commit holds back, in goh ship's
  preflight; a preflight that passes has its warnings said.
