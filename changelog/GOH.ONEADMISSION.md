* **One admission for every gate (GOH.ONEADMISSION, docs/decisions/D-0008).** A land's gate
  (`tools/load.py 0 --`) took a load place (`GOH_LOAD_GATES`, D-0003) and then a gate slot: two
  counters for one CPU, and on 2026-11-15/16 a land waited its whole 900 s `--wait-max` on a place
  while gate slots sat free. A 0-spinner `load.py` run is now admitted where every gate is, by
  `lib/admission.py`'s `admit` (memory first, then a slot, then the record), bounded by its
  `--wait-max`, and the gate it runs is inside that slot; a land and a push gate contend for the same
  slots. The shared places, `GOH_LOAD_GATES` and the pending mark are removed; an experiment holds
  off on the held gate slots as on any gate in flight, and a gate no longer waits for an experiment
  (land and push over experiment). The map of every admission controller is in the item's note.
  Pinned by `tests/test_one_admission.py`.
