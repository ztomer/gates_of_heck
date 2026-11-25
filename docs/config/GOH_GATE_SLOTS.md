# GOH_GATE_SLOTS
Area: pushes
Kind: setting
Order: 410
Default: one per eight cores, at least one (`python3 lib/gate_slot.py default`; **2** on a 16-core box: one per four let the load average reach 118-139 there, GOH.ULTRARULES)

How many full gates run their heavy steps at once, machine-wide (`lib/gate_slot.sh`,
`lib/gate_slot.py`). Taken by every full-gate entry point -- `push_gate.sh` (before its export),
`tools/load.py 0 -- <gate>` (a land's gate; bounded by its `--wait-max`, GOH.ONEADMISSION),
`local_ci.sh` (what `gate.sh --full` execs, before its first step), `round.sh`'s
`GOH_ROUND_PREFLIGHT`, the starter `tools/gate.sh --full`, `tools/pytest.sh` run by hand (bounded by
`GOH_SUITE_WAIT_MAX`, GOH.ADMITSUITE), and `structural.sh --full` run as a `tools/gate.sh`'s child,
which takes it FOR that gate.sh until it ends (so a consumer copy predating the slot holds one too)
-- and held to exit; a nested one reuses its ancestor's slot. A gate that finds every slot held
WAITS, printing each holder (repo, pid, since when) and every 30 s how long it has waited, then
`taken after Ns waiting`. The wait is charged to no step: every step ceiling (`GOH_LCI_TIMEOUT`,
`GOH_STEP_TIMEOUT`) starts inside the gate, after the slot. `0` turns the limit off; non-numeric is
exit 2. Several sessions pushing at once with no limit failed two pushes on load alone (2026-10-18:
a private consumer killed mid-test, games' Release rebuild out of its 900 s step limit at a load
average of 49.6). Set it in the environment (a machine's budget), not in `.gatesrc`.
