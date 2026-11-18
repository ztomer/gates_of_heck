* The release canary reuses a consumer's verdict while nothing that decides it moved (GOH.CANARYMEMO):
  each side's run over a consumer is kept under the side's gate content (docs, tests and *.md
  aside), binary, tools, seed store and grace, and the consumer's committed HEAD, in the estate
  state's `release_canary` entry for a day. A land under the same gates as the last spends no run
  and names the sweep it reused; a consumer whose HEAD moved is swept alone; the base side holds
  until the next release; a gate change sweeps every consumer again. `--fresh` reuses nothing.
  Verdict parsing moved to lib/canary_sides.py (the script under its 450-line cap again).
