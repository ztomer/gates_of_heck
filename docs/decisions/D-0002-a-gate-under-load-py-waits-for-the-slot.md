# D-0002 — a run with no spinners waits for the load slot by default; an experiment is told the line

- **Question:** When `tools/load.py` finds the slot held, does a gate command (`load.py 0 -- <gate>`,
  the house's one way to run a long thing) wait by default, or refuse until it is asked to wait?
- **Evidence:** On 2026-11-11 two agents met `load: another load experiment already holds the slot`
  on a 0-spinner gate run and stepped around it by running the gate bare, which loads the machine
  just the same and is tracked by nothing (no deadline, no `--status`). This session met it too:
  a `load.py --wait 0 -- tools/land.sh` was refused (exit 3) while `python3 tools/check_painted_rig.py`
  held the slot with 1 spinner, because `--wait` waited only for the gates in flight, never for the
  slot. The refusal named the holder but gave no line to run. Pinned by `tests/test_load_refusal.py`
  (all four red on the old tool).
- **Options priced:**
  1. Every run waits for the slot by default, bounded by `--wait-max`. An experiment with spinners
     would then start minutes late with nobody choosing that, on a machine whose state changed
     while it queued: a timing reproduction measures what it did not intend. Rejected for N>0.
  2. A run with NO spinners waits by default (bounded by `--wait-max`, default 15 m); a run with
     spinners refuses unless `--wait`, and the refusal prints the exact rerun line. The 0-spinner
     run measures nothing, so waiting costs no experiment its meaning, and refusing it only sends
     it round the tool. TAKEN.
  3. Keep refusing and improve only the message. The bare gate stays one keystroke shorter than the
     tracked one, which is the incident. Rejected.
  4. Let a 0-spinner run skip the slot. It would load the machine under another session's
     measurement, the thing the slot exists to prevent. Rejected.
- **Ruling:** (GOH.LOADREFUSE, 2026-11-11) `--wait` waits for the slot as well as for the gates in
  flight, under the one bound `--wait-max`. `load.py 0 -- CMD` waits for the slot without being
  asked and says so (`load: waiting for the slot`). A refusal names the holder (pid, start time,
  spinner count, command) and prints `python3 <goh>/tools/load.py --wait <the same arguments>`; a
  wait that ran out says the slot is still held and names the holder. The gate-in-flight refusal
  prints the same line.
- **Falsifier:** `tests/test_load_refusal.py` -- a refused experiment's stderr carries the holder's
  command and the exact rerun line; `--wait` starts once the holder is done; a 0-spinner run waits
  unasked; the slot wait is bounded by `--wait-max`. In the estate: an agent report of a gate run
  bare "because load.py refused" shows the ruling has not made the right path the easy one.
- **Re-opened:** 2026-11-11 by D-0003 (gates share the slot, at most three).
- **Re-open when:** a 0-spinner run waits its whole `--wait-max` behind experiments more than once a
  week (gates would then need a slot of their own), or an experiment needs to start beside a gate
  run on purpose.
- **Lives in:** `lib/load_slot.py`, `tools/load.py`, `tests/test_load_refusal.py`
