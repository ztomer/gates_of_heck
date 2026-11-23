* A land's pre-step sends the chosen tests whose module `tests/linux_safe.txt` records to the remote
  worker AT THE SAME TIME as the Mac runs the rest (`lib/remote_lane.py`), and joins the worker's
  passes to its verdict record, so the pre-step costs the longer of the two, not their sum. Skip
  parity is checked against the recorded Mac skip set `tests/mac_skips.txt`. The land never fails
  because of the worker: unreachable, busy, short of scratch, off-pin, a skip mismatch or an
  unreadable junit has the Mac run those tests itself and say why, and a test that failed only on
  the worker is re-run on the Mac, whose verdict stands. `GOH_LAND_REMOTE=off` keeps the pre-step
  on the Mac. `python3 lib/remote_lane.py record LINUX.xml MAC.xml` rewrites both lists.
* `goh remote-shard` keeps one tree per lane slot on the worker (`trees/<slot>/`, the slot keyed
  by the checkout's path, its own lock), so two lanes never queue on one tree; `--no-local` hands
  a fallback back to the caller (exit 3); `--mac-skips` takes the recorded skip set; a remote junit
  that does not parse is a fallback, not a crash.
