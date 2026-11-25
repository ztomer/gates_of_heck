* The land and ship time account closes (GOH.TIMEACCOUNT): `goh land-profile --account` prints each
  group's wall as named phases plus `unaccounted` -- every container's unnamed rest (the e2e less
  its phases, the lander run less pre-land and gate, the gate less slot wait and steps), never a
  remainder taken as a phase -- with a FAULT line over `GOH_ACCOUNT_UNACCOUNTED_PCT` (5%); the
  seconds thrown away by cause; each gate step's yield (runs, seconds, red, the only red); and five
  readings: the pre-step's phases, the enqueue check, tests reused and refused with the read that
  refused them, CPU by owner and lane time by kind. Replayed over the existing rows (14 days): 345
  lands, 382.7 h, 21.1 h (5.5%) unaccounted -- the fault line fires; ships 3.4%; the structural
  double gate (`path_invariance.py -- structural.sh --full`) ran in 275 lands, 197 min, red once and
  never the only red; 43 lanes, 45 h: model 45%, waits 29%, tests 12%.
* Every start has an end row (`lib/run_ends.py`, goh.db 0024 `run_ends`, `run_phases`): a check
  (`lib/check_jobs.py`, the watcher's kill included) and a pre-step (`lib/land_prestep.py`) write
  their row before they run and close it finished, red (by cause), stopped, refused or carried; a
  start whose process is gone is swept `stopped` (`died`) by every reader.
* CPU by owner (`lib/cpu_owners.py`, goh.db `cpu_owners`): the load sampler's own process table,
  every `GOH_CPU_OWNERS_PERIOD_S` (120 s), booked to lanes, gates, the idle suite and the rest by
  `GOH_CPU_OWNERS` working-directory markers; its own cost is the `sampler` row (measured 4.9 ms
  per window over 1047 processes: 0.004% of one core). Lane time by kind (`lib/lane_time.py`) reads
  the agent transcripts, read only, kinds in `GOH_LANE_TIME_KINDS`.
