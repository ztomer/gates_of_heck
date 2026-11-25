# GOH_TIMINGS_KIND
Area: runtime
Kind: internal
Order: 160
Default: set by the gate

What a gate's steps are, so the timings report reads each step by the figure that holds for its kind
(BACKLOG 5.3): `gates/rust_gate.sh` exports `rust`, divided by the rust control
`tools/gate_profile.sh` runs before and after the gate; `gates/structural.sh` exports `native`, read
as raw CPU (`lib/step_timings.py` `RAW_KINDS`). Every writer (`lib/step_timings.py`, the native `goh
step`, the native tier's in-process steps) records it as `kind`. A step's child does not inherit it:
a gate the step runs declares its own, and the report gives a step that runs a gate that gate's kind
when every kinded row under it agrees. Not a setting: a gate declares it for its own steps.
