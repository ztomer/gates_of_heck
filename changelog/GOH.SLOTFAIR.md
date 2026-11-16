* Gate slots are fair (GOH.SLOTFAIR). A commit gate no longer waits behind long gates: a gate's
  class (`GOH_GATE_CLASS`) picks its lane, and `gates/commit_gate.sh` marks the `tools/gate.sh
  --staged` it runs `short`, admitted by its own lane of `GOH_GATE_SLOTS` slots (`short-<i>`) that
  no push, land or `load.py 0` run takes (2026-11-16: `.loop`'s commit gate, ~3 min of work, waited
  38+ min behind a 49-min cargo test and a 15-min push gate; now it waits only behind other commit
  gates). In the long lane a freed slot goes to the longest live waiter of the highest rank: each
  waiter holds a flock'd ticket in `<slot dir>/waiting/`, push gates and lands (`ship`) rank ahead
  of every other gate (`work`), and a SIGKILLed waiter's ticket is reaped, never blocking the queue
  (2026-11-15: a monitor push waited 20m30s while six later gates took freed slots).
