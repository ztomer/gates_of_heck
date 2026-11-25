# GOH_STEP_BUDGET
Area: local-ci
Kind: internal
Order: 180
Default: set by `local_ci.sh`

The CALLER's ceiling, never a repo's: `local_ci.sh` exports it to each step as that step's own
ceiling (`GOH_LCI_TIMEOUT`), and every `goh_step` inside (both tiers) whose repo sets no
`GOH_STEP_TIMEOUT` falls back to it before the 1800 s constant. The hosted workflow gave
`./gates/rust_gate.sh` 2700 s and its coverage step was still cut at 1800 s, because rust_gate.sh
drops an inherited `GOH_STEP_TIMEOUT` (GOH.COVPHASES, run 43331499075). Verdict-free (a step that
passed under a ceiling passes under a larger one), so no proven key varies with who started the
gate. Non-numeric is exit 2 (`tests/test_step_budget.py`). Not a setting.
