# D-0003 — gates share the load slot (at most three); an experiment with spinners holds it alone

- **Question:** What must `tools/load.py`'s machine-wide slot exclude? Until now a run with no
  spinners (`load.py --wait 0 -- <gate>`, D-0002) took it as exclusively as a load experiment.
- **Evidence:** On 2026-11-11 every agent's gate waited 10 to 30+ minutes for the slot: nine faults
  mapped to GOH.GATESLOT, the queue ran five deep and stalled ~20 minutes behind one
  `git push` holder, one push held it 22 minutes, and two agents hit their 90-minute stop waiting.
  Measured the same day on this 16-core machine (two rounds, a CPU-bound gate: goh's 47
  `tests/test_check_*.py` files at the suite's own `-n 12`), wall time per gate at k at once:
  k=1 20.9 / 24.1 s, k=2 39.8 / 39.2 s, k=3 56.4 / 56.5 s, k=4 74.8 / 73.4 s. Throughput is flat
  from 1 to 4 (k gates finish in about k times one gate's time), so running gates together costs
  the machine nothing, and it removes the queue behind a holder that is waiting on a network or a
  lock rather than the CPU. In the first round at k=4 all four runs failed (rc=1); at k<=3 none
  ever did. Pinned by `tests/test_load_gate_share.py` (red on the old tool: a second gate waited
  for the first).
- **Options priced:**
  1. Keep the slot exclusive for gates. The 2026-11-11 stalls, unchanged. Rejected.
  2. Let a 0-spinner run skip the slot entirely. An experiment could then start beside any number
     of gates, and gates beside an experiment: the measurement D-0002 protects. Rejected.
  3. Gates take a SHARED place, at most `GOH_LOAD_GATES` (default 3) at once; a run with spinners
     takes the slot alone; an experiment that waits leaves a pending mark, so a gate arriving
     behind it waits for it (no starvation by a stream of gates). TAKEN.
  4. A CPU share (taskpolicy / nice) instead of a bound. Throughput is already flat; a share does
     not bound the load-sensitive budgets (a private consumer's frame clock) that a count does. Rejected.
- **Ruling:** (GOH.GATESLOT, 2026-11-11) `load.py 0 -- CMD` holds `<load dir>/gates/<pid>.json`;
  at most `GOH_LOAD_GATES` (default 3, `0` = no bound) share it. `load.py N -- CMD` with N>0
  holds `registry.json` alone and is refused (or with `--wait`, waits) while any gate shares the
  slot; while it waits, `pending.experiment` makes new gates wait behind it. A dead owner's place
  is pruned at the next claim. The heavy-step bound inside a gate stays `lib/gate_slot.sh`
  (`GOH_GATE_SLOTS`, one per four cores). `lib/load_bare.py` still refuses a bare build only
  beside an EXPERIMENT; beside gates a bare build is one more gate, not a spoiled measurement.
- **Falsifier:** `tests/test_load_gate_share.py`. In the estate: a load-sensitive gate (a private
  consumer's frame clock) that fails only when two or three other gates share the slot means the bound is too
  high for it; a gate wait over 10 minutes with fewer than three gates sharing means the slot is
  held by something else.
- **Re-open when:** a gate's own budget fails beside two other gates more than once a week, or the
  machine's core count changes (re-measure: `k` at once, two rounds).
- **Lives in:** `lib/load_slot.py`, `tools/load.py`, `tests/test_load_gate_share.py`
