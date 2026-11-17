# D-0009 — gates stay admitted by a count of slots; a CPU-weighted admission is rejected on a day's replay

- **Question:** admission counts gates (`GOH_GATE_SLOTS`, 2 on 16 cores), and a goh land gate
  burns ~5-7 cores (median 5.21 cpu-s per wall-s over the 29 goh lands of 2026-11-17), so the
  review asked (GOH.CPUADMIT) whether two slots saturate the box and stretch every gate, and
  whether admitting each gate by its CPU weight from the one timing record (`lib/capacity.py`
  `gate_weight`: the median cpu-s per wall-s of its repo's runs) would do better.
- **Evidence:** the replay of 2026-11-17 (UTC; `python3 lib/admit_replay.py --day 2026-11-17`,
  the 79 push and land gates the timing record held through 18:36 UTC, 16 cores, first come first served, a fluid share
  of the cores; summed seconds over the day):

  | model | rule | wait | gate | lane (wait + gate) |
  |---|---|---|---|---|
  | recorded demand, no other work | count:2 | 308 | 32765 | 33073 |
  | | cpu:16 | 0 | 32765 | 32765 (-0.9%) |
  | | cpu:12 | 1201 | 32765 | 33965 (+2.7%) |
  | | cpu:8 | 3305 | 32765 | 36069 (+9.1%) |
  | recorded demand, 16 cores of other work | count:2 | 1967 | 42665 | 44633 |
  | | cpu:16 | 0 | 43031 | 43031 (-3.6%) |
  | | cpu:8 | 7078 | 42310 | 49389 (+10.7%) |
  | demand x2.7 (a goh gate fills an idle box) | count:2 | 0 | 12361 | 12361 |
  | | cpu:16 | 0 | 12361 | 12361 (0%) |
  | | cpu:8 | 535 | 12303 | 12838 (+3.9%) |

  Calibration: the count:2 replay's 308 s of wait sits beside the 421 s the landing queue recorded
  that day (two land waits, 142 s and 279 s; seven of nine lands waited 0 s); a push gate's own
  wait is not in the record (its span starts after the slot), so every replayed wait is a lower
  bound. The long lane is not where the load is: the day's push and land gates spent 130346 cpu-s
  over 45044 s, a mean of 2.89 cores, while the 1-minute load at their starts had a median of
  69.6 (19.5-160.3); every lane-start reading (`lane-starts.jsonl`) put the gates in flight at
  0.00-0.54 cores per core against a 5-minute load of 1.7-7.4 per core, and the first (15:07 UTC)
  read a load of 60 with no slot held. Lands serialize on the land lock, so two goh gates are
  rarely in flight together (peak concurrency 2 under the count, 3-4 under cpu:16). Caveat: cpu_s
  is the reaped tree, so a compile done by the sccache server is not in it; tripled, the gates
  would still be under a seventh of the load.
- **Options priced:**
  1. Keep the count of 2. Lane time 33073 s on the quiet model, 44633 s under background. TAKEN.
  2. `cpu:16` (admit while the weights in flight plus the head's fit the cores). Saves 308 s
     (0.9%) quiet and 1602 s (3.6%) under background, all of it wait, by admitting more light
     pushes beside a land; it never refuses what the count admits on today's weights, so it
     cannot relieve the load the item was filed for, and under background its gate time is 366 s
     worse (more gates sharing the same cores). The weight it would read is also wrong today for
     every lane worktree (follow-up below). Rejected: under 5% either way, against a new
     admission path in every gate.
  3. `cpu:8` or `cpu:12` (a budget under the cores, to leave room for other work). Waits of
     1201-7078 s against the count's 308-1967, for at most 0.8% less gate time. Rejected.
  4. Find what does make the load (the short lane's pre-steps, agents' own `load.py 0 -- cargo
     test` runs, sessions' builds and tests outside any slot) and admit THAT. Not this item's to
     land; filed as the follow-up below.
- **Ruling:** (GOH.CPUADMIT, 2026-11-17) Gates stay admitted by a count of slots (D-0008,
  `lib/gate_slot.py` `default_slots`). A CPU-weighted rule is not built: on the day's record it
  moves lane time by under 4%, and the box's load is 96% outside the gates it would weigh.
  Follow-ups: (a) measure the CPU of the work outside the long lane (pre-steps and short-lane
  gates carry no cpu-s row; agents' builds carry nothing) before any admission is changed again;
  (b) fixed with this ruling: a slot holder in a lane worktree was named by its directory
  (`wt`, `wt2`: `tools/load.py` labels by `Path.cwd().name`), so `capacity.gate_weight` weighed a
  goh lane's gate by every repo's median (1.27-1.63) instead of goh's (5.21); `lib/capacity.py`
  now names a holder by its record's repository key among the registered repos.
- **Falsifier:** `tests/test_admit_replay.py` pins the instrument (each figure computed by hand,
  both directions: light gates wait under a count, heavy gates saturate under it). In the estate:
  a day whose replay moves lane time by 5% or more between `count:2` and `cpu:16`.
- **Re-open when:** a replay of any day shows `cpu:16` 5% or more under `count:2` in lane time,
  the gates in flight read over 1.0 core per core at a lane start, the slot count changes, or the
  work outside the long lane is admitted (then its weight is the unit to replay).
- **Lives in:** `lib/admit_replay.py`, `tests/test_admit_replay.py`, `lib/gate_slot.py`,
  `lib/capacity.py`
