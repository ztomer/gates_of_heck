# GOH_PRESTEP_CAP
Area: claude-hooks
Kind: setting
Order: 40
Default: the curve's peak (`1` on 2026-11-24)

Land pre-steps in flight (enqueues in their pre-step: a live checkout-slot claimer whose request is
not filed) above which there is no room (GOH.LOADCAP, owner 2026-11-23: "choose the value that will
give us the most overall throughput"). Unset, `lib/load_cap.py` reads it at every reading: the peak
of landed items per busy hour against pre-steps in flight over the newest week of land records, a
band needing 5 busy hours; `1` when none has. Measured 2026-11-24: 1 in flight 2.44 landed/h (n=39
h), 2 in flight 2.09 (n=11); the lander's gate took 604/932/1515 s beside 0/1/2 pre-steps
(n=183/67/36) while the load band barely moved it. `python3 lib/load_cap.py` prints the curve.
