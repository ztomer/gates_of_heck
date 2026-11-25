# GOH_PROBE_PAUSE_S
Area: runtime
Kind: setting
Order: 420
Default: `0`

The gate-slot race probe's seam (`checks/_gate_slot_probe.py`): how long the INSTRUMENTED writer
pauses between the wait report's two writes, which is what makes the E39 race red-provable instead
of once-in-a-while. Read by the instrumented COPY of `lib/gate_slot.py` only; the real writer never
reads it. Not user config.
