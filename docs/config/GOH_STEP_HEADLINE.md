# GOH_STEP_HEADLINE
Area: runtime
Kind: setting
Order: 100
Default: set by `goh_step`

A file a step may write ONE line to: a failed step then headlines that line (`<gate>: <line>`)
instead of `<label> failed (...)`. The coverage step names the failing tests that starved the
measurement this way (GOH.COVHEADLINE). Internal: `goh_step` sets it per step.
