# D-0008 — every gate is admitted by a gate slot; the load places are gone; land and push over experiment

- **Question:** goh admitted one machine's gates through two counters that did not know each
  other: the gate slots (`lib/gate_slot.py`, `GOH_GATE_SLOTS`, 2 on 16 cores) for push and full
  gates, and the load places of D-0003 (`tools/load.py 0 --`, `GOH_LOAD_GATES`=3) for a land's
  gate, which then took a gate slot inside too. Which one admits a gate?
- **Evidence:** 2026-11-15/16: a land waited 900 s (its whole `--wait-max`) on a load place held
  by a post-land profiling run while gate slots sat partly free; waits of 7-25 min in every lane;
  two items for the same no-ticket defect in two queues (GOH.SLOTFAIR, GOH.LOADQUEUEORDER). A
  land's gate counted twice (a place and a slot), a push gate once, an experiment never in the
  slots. The map of every controller is GOH.ONEADMISSION's note.
- **Options priced:**
  1. Keep both. The 900 s wait, unchanged. Rejected.
  2. Raise `GOH_LOAD_GATES`. Still two counters for one CPU, and the slot bound inside the gate
     still applies. Rejected.
  3. A 0-spinner `load.py` run is admitted by a gate slot through the one entry every gate takes
     (`lib/admission.py` `admit`: memory, then a slot, then the record), bounded by its `--wait-max`; the
     gate it runs is inside that slot and takes none. The shared places, `GOH_LOAD_GATES` and the
     pending mark go. An experiment holds off on the gates in flight, which now include the held
     slots; a gate no longer waits for an experiment. TAKEN.
  4. Make an experiment take every gate slot (a writer over the counting semaphore). Starves
     every push for an experiment's length, the inverse of the priority asked for. Rejected.
- **Ruling:** (GOH.ONEADMISSION, 2026-11-16) A gate -- push, full, or a land's through
  `load.py 0` -- is admitted only by a gate slot. Land and push rank over experiment: a gate starts
  beside a running experiment (the experiment records its baseline, as it already did beside push
  gates), and an experiment waits (bounded) for the gates in flight when it asked. D-0003's
  anti-starvation mark is retired with the places: a stream of gates can hold an experiment off
  for its `--wait-max`, which is the priority, stated. Supersedes D-0003.
- **Falsifier:** `tests/test_one_admission.py` -- a `load.py 0` run waits for the slot a
  `gate_slot.sh` holder holds and starts when it is freed; its gate takes no second slot; its
  wait expires naming the holder; an experiment holds off beside it (exit 75); it does not wait
  for an experiment. In the estate: a land wait on anything but a gate slot or the land lock.
- **Re-open when:** an experiment cannot get a quiet window inside its `--wait-max` more than once
  a week (then a ticketed queue with an experiment class, GOH.SLOTFAIR), or the slot count changes.
- **Lives in:** `lib/admission.py`, `lib/gate_slot.py`, `lib/load_slot.py`, `lib/gates_in_flight.py`, `tools/load.py`,
  `lib/lock_order.tsv`, `tests/test_one_admission.py`
