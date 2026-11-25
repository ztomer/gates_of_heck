# GOH_PROFCARRY_AUDIT
Area: local-ci
Kind: internal
Order: 190
Default: unset

Not a setting: `fresh` in the idle lander's and the ship's full instrumented run (`lib/full_run.py`
INSTRUMENTED), where the Rust coverage step compares its per-crate summary with the land's claim on
the tree instead of claiming (`lib/prof_carry.py`, GOH.COVCARRY2). Verdict-free: neither ever
changes the step's exit.
