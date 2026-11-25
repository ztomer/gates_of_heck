# GOH_SUITE_WAIT_MAX
Area: python
Kind: setting
Order: 60
Default: `900` (`tools/load.py`'s `--wait-max` default)

How long a suite run by hand (`tools/pytest.sh` outside a gate) waits for its admission, in seconds
-- the memory wait counted inside it (GOH.ADMITSUITE). It comes in where every gate does
(`lib/admission.py` through `lib/gate_slot.sh`): a whole run in the long lane as `work` (behind
every push and land), a narrowed run (a path argument) in the short lane beside commit gates;
`GOH_GATE_CLASS` overrides. Past the bound it exits 75 naming the holders and runs nothing. Inside a
gate, or with `GOH_GATE_SLOTS=0`, it takes no slot (memory is still asked). Non-numeric is exit 2.
