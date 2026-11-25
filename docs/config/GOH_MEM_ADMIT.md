# GOH_MEM_ADMIT
Area: pushes
Kind: setting
Order: 500
Default: on

Memory admission (`lib/mem_admit.py`, GOH.MEMADMIT): a new gate (the gate slot's holder, before it
takes a slot) and a new suite (`tools/pytest.sh` outside a gate) WAIT while memory is low, saying
why when it starts and every 30 s, and `starts after Ns waiting` when it lifts. `0` turns it off;
the suite's fixtures turn it off (`tests/host_locks.py`), their gates run inside a suite already
admitted.
