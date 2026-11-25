# GOH_GATE_VERDICTS
Area: verdict-store
Kind: setting
Order: 60
Default: retired

The per-ship `green <sha>` file the push gate wrote under `goh ship` (GOH.VERDICTAPI); gone with
GOH.ONEVERDICT, the push records into the store above. Neither set nor read; `lib/pin_sweep.py`
still drops it from a child's environment.
