# GOH_SUITE_SHARD
Area: coverage
Kind: internal
Order: 80
Default: set by the coverage part `GOH_COV_PART=K/N` only

The suite (`tools/pytest.sh`) runs only the K-th of N disjoint parts (tests/_shard.py), partitioned
from the collection alone, each xdist group kept whole; `tests/_schedule.py` pops it at entry and
hands it to every xdist worker, so no test's child inherits it. `rust_gate.sh` refuses it from a
caller and `pytest.sh` refuses it beside a land's verdict record, so no cache key or recorded
verdict is ever taken under it. Not a setting.
